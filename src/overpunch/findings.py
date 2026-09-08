"""Rules that turn measured statistics into findings.

Each rule states what it observed, over how many records, and — where the
consequence is arithmetic — what the wrong reading actually costs. A rule that
cannot show its working does not belong here.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from .layout import Field, Layout, Usage
from . import predicates as pred
from .probe import Finding, FieldStats, field_key


def _pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):.1f}%" if total else "n/a"


def evaluate(layout: Layout, stats: dict[str, FieldStats]) -> list[Finding]:
    out: list[Finding] = []
    for fld in layout.elementary_fields():
        st = stats.get(field_key(fld))
        if st is None or st.examined == 0:
            continue
        out.extend(_field_rules(fld, st))
    out.extend(_condition_rules(layout, stats))
    order = {"critical": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda f: (order[f.severity], f.field))


def _field_rules(fld: Field, st: FieldStats) -> list[Finding]:
    out: list[Finding] = []
    pic = fld.pic
    signed_bytes = st.overpunch_negative + st.overpunch_positive

    if pred.signed_bytes_present(fld, st):
        if pred.sign_is_load_bearing(fld, st):
            delta = st.sum_abs - st.sum_correct
            out.append(Finding(
                code="TRAILING_SIGN", severity="critical", field=fld.name,
                claim=("the last byte carries the sign, not a digit; dropping it "
                       "inverts the negative records"),
                evidence={"negative": f"{st.overpunch_negative:,}",
                          "positive": f"{st.overpunch_positive:,}",
                          "share_negative": _pct(st.overpunch_negative, st.examined)},
                records=st.examined,
                impact=(f"correct total {st.sum_correct:,.2f}; "
                        f"sign ignored {st.sum_abs:,.2f} "
                        f"(overstated by {delta:,.2f})")))
        elif pred.sign_present_but_inert(fld, st):
            out.append(Finding(
                code="SIGN_PRESENT_ALL_POSITIVE", severity="info", field=fld.name,
                claim=("the final byte is a sign, not a digit, but every record in "
                       "this file is positive; a digits-only read happens to agree "
                       "here and will stop agreeing the first time a credit appears"),
                evidence={"positive": f"{st.overpunch_positive:,}", "negative": "0"},
                records=st.examined))
        else:
            out.append(Finding(
                code="UNDECLARED_SIGN", severity="critical", field=fld.name,
                claim=("copybook declares this field unsigned (PIC 9) but the bytes "
                       "carry a sign in the final position; the copybook is wrong "
                       "about the data"),
                evidence={"signed_records": f"{signed_bytes:,}",
                          "share": _pct(signed_bytes, st.examined),
                          "declared": pic.raw},
                records=st.examined,
                impact=(f"read as declared {st.sum_correct:,.2f}; "
                        f"honouring the sign {st.sum_forced_signed:,.2f} "
                        f"(difference {st.sum_correct - st.sum_forced_signed:,.2f})")))

    if pred.implied_decimal(fld, st):
        if fld.usage is Usage.DISPLAY:
            claim = (f"{pic.scale} implied decimal place(s); the bytes contain no "
                     f"decimal point, so a digits-only read is 10^{pic.scale} "
                     f"too large")
            impact = (f"correct total {st.sum_correct:,.2f}; "
                      f"digits-only read {st.sum_naive:,.0f}")
        else:
            claim = (f"{pic.scale} implied decimal place(s) on a "
                     f"{fld.usage.value} field; the value is stored unscaled, and "
                     f"a decoder that returns the integer leaves the caller "
                     f"10^{pic.scale} too large")
            impact = (f"correct total {st.sum_correct:,.2f}; unscaled "
                      f"{st.sum_correct.scaleb(pic.scale):,.0f}")
        out.append(Finding(
            code="IMPLIED_DECIMAL", severity="warn", field=fld.name, claim=claim,
            evidence={"declared": pic.raw, "scale": pic.scale,
                      "usage": fld.usage.value},
            records=st.examined, impact=impact))

    if pred.width_underfill(fld, st):
        out.append(Finding(
            code="WIDTH_UNDERFILL", severity="warn", field=fld.name,
            claim=(f"declared {pic.int_digits} integer digits but no record uses "
                   f"more than {st.max_significant_digits}; the source may be "
                   f"narrower than the copybook, or values may be truncated"),
            evidence={"declared_digits": pic.int_digits,
                      "widest_observed": st.max_significant_digits},
            records=st.examined))

    if pred.populated_filler(fld, st):
        sample = st.distinct.most_common(3)
        out.append(Finding(
            code="POPULATED_FILLER", severity="warn", field="FILLER "
                 f"@ offset {fld.offset}",
            claim=("declared FILLER but carries data; something is in this field "
                   "that the copybook does not describe"),
            evidence={"non_blank": f"{st.examined - st.blank:,}",
                      "distinct_values": len(st.distinct),
                      "examples": ", ".join(repr(v) for v, _ in sample)},
            records=st.examined))

    if pred.never_populated(fld, st):
        out.append(Finding(
            code="NEVER_POPULATED", severity="info", field=fld.name,
            claim="field is blank, zero or low-values in every record examined",
            evidence={"blank": f"{st.blank:,}", "zero": f"{st.all_zero:,}",
                      "unset": f"{st.unset:,}", "of": f"{st.examined:,}"},
            records=st.examined))

    if pred.float_format_contradicted(fld, st):
        ev = pred.float_evidence(fld, st)
        order = pred.float_byteorder(fld, st)
        want = {"hfp": "hfp", ("ieee", "big"): "ieee",
                ("ieee", "little"): "ieee-le"}.get(
                    "hfp" if ev == "hfp" else (ev, order), "ieee")
        names = {"hfp": "IBM hexadecimal float", "ieee": "big-endian IEEE 754",
                 "ieee-le": "little-endian IEEE 754"}
        out.append(Finding(
            code="FLOAT_FORMAT_MISMATCH", severity="critical", field=fld.name,
            claim=(f"this column is being read as {names[st.float_format]}, and "
                   f"the bytes say it is {names[want]}; nothing in a "
                   f"COMP-1/COMP-2 field records which one wrote it - not the "
                   f"format and not the byte order - so both are measured"),
            evidence={"non_zero_values": f"{st.float_nonzero:,}",
                      "unnormalisable_as_hex_float": f"{st.float_unnormalised:,}",
                      "share": _pct(st.float_unnormalised, st.float_nonzero),
                      "distinct_first_byte": f"{len(st.float_head_bytes)}",
                      "distinct_last_byte": f"{len(st.float_tail_bytes)}",
                      "reading_in_force": st.float_format},
            records=st.examined,
            impact=(f"re-read with --float-format {want}. The wrong reading "
                    f"does not fail - it returns ordinary numbers, off by "
                    f"orders of magnitude. A real compiler proved this: "
                    f"GnuCOBOL on x86 wrote 1.72708 as 15 11 dd 3f, which read "
                    f"big-endian is 2.9457e-26")))

    if pred.float_format_confirmed(fld, st):
        out.append(Finding(
            code="FLOAT_FORMAT_CONFIRMED", severity="info", field=fld.name,
            claim=(f"read as {st.float_format}, and the bytes support it - both "
                   f"the format and the byte order were measured over the "
                   f"column, not assumed from a default"),
            evidence={"non_zero_values": f"{st.float_nonzero:,}",
                      "unnormalisable_as_hex_float": f"{st.float_unnormalised:,}",
                      "distinct_magnitudes": f"{len(st.float_exponents)}",
                      "distinct_first_byte": f"{len(st.float_head_bytes)}",
                      "distinct_last_byte": f"{len(st.float_tail_bytes)}"},
            records=st.examined))

    if pred.float_format_undecidable(fld, st):
        out.append(Finding(
            code="FLOAT_FORMAT_UNDECIDABLE", severity="warn", field=fld.name,
            claim=(f"this column cannot settle its floating-point format, or "
                   f"which end of it holds the exponent, so the reading in "
                   f"force ({st.float_format}) remains a default, not a "
                   f"measurement"),
            evidence={"non_zero_values": f"{st.float_nonzero:,}",
                      "needed": f"{pred.FLOAT_SAMPLE_FLOOR:,}",
                      "distinct_magnitudes": f"{len(st.float_exponents)}",
                      "needed_magnitudes": f"{pred.FLOAT_EXPONENT_SPREAD}",
                      "distinct_first_byte": f"{len(st.float_head_bytes)}",
                      "distinct_last_byte": f"{len(st.float_tail_bytes)}"},
            records=st.examined,
            impact=("a true IEEE column shows values that cannot be normalised "
                    "hex float at 4.35% (4-byte) or 26.15% (8-byte) - but only "
                    "when the values span more than one magnitude. Too few of "
                    "them, or all inside one binade, and NEITHER format "
                    "produces one, so seeing none proves nothing")))

    if pred.binary_byteorder_contradicted(fld, st):
        observed = pred.binary_byteorder(fld, st)
        out.append(Finding(
            code="BINARY_BYTE_ORDER", severity="critical", field=fld.name,
            claim=(f"this binary field is being read {st.binary_order}-endian "
                   f"and the bytes say it is {observed}-endian; COMP-5 is "
                   f"defined as NATIVE order, and nothing in the file records "
                   f"which machine's"),
            evidence={"distinct_first_byte": f"{len(st.binary_head_bytes)}",
                      "distinct_last_byte": f"{len(st.binary_tail_bytes)}",
                      "reading_in_force": st.binary_order},
            records=st.examined,
            impact=(f"re-read with --binary-byteorder {observed}. The high-order "
                    f"end of a real column varies little because magnitudes "
                    f"cluster; here it is the {'last' if observed == 'little' else 'first'} "
                    f"byte. Read the wrong way round the values are still "
                    f"integers, just different ones")))

    if pred.binary_exceeds_pic(fld, st):
        limit = 10 ** pic.digits - 1
        out.append(Finding(
            code="BINARY_EXCEEDS_PIC", severity="critical", field=fld.name,
            claim=(f"declared {pic.raw} COMP, which a standard compiler "
                   f"truncates to {limit:,}, but this field holds larger "
                   f"values; it is COMP-5 or was compiled TRUNC(BIN), and the "
                   f"copybook does not say so"),
            evidence={"over_limit": f"{st.binary_over_pic:,}",
                      "share": _pct(st.binary_over_pic, st.examined),
                      "largest_seen": f"{st.binary_max_abs:,}",
                      "pic_allows": f"{limit:,}"},
            records=st.examined,
            impact=("one value above the limit is enough - a standard COMP "
                    "field cannot contain one. Anything downstream that "
                    "believes the PICTURE will size a column too small")))

    if pred.unknown_sign_byte(fld, st):
        out.append(Finding(
            code="UNKNOWN_SIGN_BYTE", severity="critical", field=fld.name,
            claim=("the final byte is neither a digit, nor any sign convention "
                   "this decoder implements, nor the padding of an unset field; "
                   "reading it as digits silently drops the last one"),
            evidence={"records": f"{st.unknown_sign_byte:,}",
                      "share": _pct(st.unknown_sign_byte, st.examined),
                      "bytes": ", ".join(
                          f"{b} x{n:,}"
                          for b, n in st.unknown_sign_values.most_common(4))},
            records=st.examined,
            impact=("the code page or the sign convention is wrong; an "
                    "ASCII-native zoned file read as EBCDIC lands here")))

    if pred.invalid_packed(fld, st):
        out.append(Finding(
            code="INVALID_PACKED", severity="critical", field=fld.name,
            claim=("declared COMP-3 but the nibbles are not valid packed decimal; "
                   "the usage or the offset is wrong"),
            evidence={"bad_records": f"{st.invalid_packed:,}",
                      "share": _pct(st.invalid_packed, st.examined)},
            records=st.examined))

    if st.undecodable and st.undecodable > st.examined * 0.01:
        out.append(Finding(
            code="ENCODING_SUSPECT", severity="warn", field=fld.name,
            claim="bytes do not decode cleanly in the chosen code page",
            evidence={"undecodable": f"{st.undecodable:,}",
                      "share": _pct(st.undecodable, st.examined)},
            records=st.examined))

    return out


def _condition_rules(layout: Layout, stats: dict[str, FieldStats]) -> list[Finding]:
    out: list[Finding] = []
    for fld in layout.elementary_fields():
        if not fld.conditions:
            continue
        st = stats.get(field_key(fld))
        owner: dict[str, list[str]] = {}
        for name, values in fld.conditions.items():
            for v in values:
                owner.setdefault(v, []).append(name)

        # via the shared predicate: this rule had its own inline copy and never
        # got the narrowing, so it kept reporting the ordinary
        # "VALUE 'F' 'N'" validity idiom as nine critical findings
        for value in pred.duplicated_condition_values(fld):
            names = [n for n in owner.get(value, [])
                     if len(fld.conditions[n]) == 1]
            out.append(Finding(
                code="AMBIGUOUS_CONDITION", severity="critical", field=fld.name,
                claim=(f"value {value!r} is claimed by {len(names)} different "
                       f"condition names, each as its only value; which one is "
                       f"meant cannot be settled from the copybook or the data"),
                evidence={"value": value, "names": ", ".join(names)},
                records=st.examined if st else 0))

        if st:
            unset, examined = pred.unset_share(fld, st)
            if unset and examined and unset < examined:
                out.append(Finding(
                    code="MOSTLY_UNSET", severity="info", field=fld.name,
                    claim=("this coded field carries no value in most records; "
                           "low-values are how a mainframe says 'not set'"),
                    evidence={"unset": f"{unset:,}", "of": f"{examined:,}",
                              "share": _pct(unset, examined)},
                    records=examined))
            uncovered = pred.uncovered_values(fld, st)
            if uncovered:
                out.append(Finding(
                    code="UNCOVERED_VALUE", severity="warn", field=fld.name,
                    claim=("values appear in the data that no 88-level accounts for; "
                           "code that switches on the declared conditions will fall "
                           "through for these records"),
                    evidence={"values": ", ".join(
                                  f"{v!r} x{c:,}" for v, c in
                                  sorted(uncovered.items(), key=lambda kv: -kv[1])[:5]),
                              "records_affected": f"{sum(uncovered.values()):,}"},
                    records=st.examined))
    return out
