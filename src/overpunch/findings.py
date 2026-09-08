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
