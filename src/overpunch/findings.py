"""Rules that turn measured statistics into findings.

Each rule states what it observed, over how many records, and — where the
consequence is arithmetic — what the wrong reading actually costs. A rule that
cannot show its working does not belong here.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from .layout import Field, Layout, Usage
from .probe import Finding, FieldStats


def _pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):.1f}%" if total else "n/a"


def evaluate(layout: Layout, stats: dict[str, FieldStats]) -> list[Finding]:
    out: list[Finding] = []
    for fld in layout.elementary_fields():
        st = stats.get(fld.name)
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

    if pic.is_numeric and fld.usage is Usage.DISPLAY and signed_bytes:
        if pic.signed and st.overpunch_negative:
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
        elif pic.signed:
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

    if pic.is_numeric and pic.scale and fld.usage is Usage.DISPLAY:
        out.append(Finding(
            code="IMPLIED_DECIMAL", severity="warn", field=fld.name,
            claim=(f"{pic.scale} implied decimal place(s); the bytes contain no "
                   f"decimal point, so a digits-only read is 10^{pic.scale} too large"),
            evidence={"declared": pic.raw, "scale": pic.scale},
            records=st.examined,
            impact=(f"correct total {st.sum_correct:,.2f}; "
                    f"digits-only read {st.sum_naive:,.0f}")))

    if pic.is_numeric and pic.scale == 0 and st.max_significant_digits and \
            st.max_significant_digits <= pic.int_digits - 1:
        out.append(Finding(
            code="WIDTH_UNDERFILL", severity="warn", field=fld.name,
            claim=(f"declared {pic.int_digits} integer digits but no record uses "
                   f"more than {st.max_significant_digits}; the source may be "
                   f"narrower than the copybook, or values may be truncated"),
            evidence={"declared_digits": pic.int_digits,
                      "widest_observed": st.max_significant_digits},
            records=st.examined))

    if fld.is_filler and st.examined and st.blank < st.examined:
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

    if st.examined and (st.blank == st.examined or st.all_zero == st.examined):
        out.append(Finding(
            code="NEVER_POPULATED", severity="info", field=fld.name,
            claim="field is blank or zero in every record examined",
            evidence={"blank": f"{st.blank:,}", "zero": f"{st.all_zero:,}"},
            records=st.examined))

    if st.invalid_packed:
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
        st = stats.get(fld.name)
        claimed: Counter = Counter()
        owner: dict[str, list[str]] = {}
        for name, values in fld.conditions.items():
            for v in values:
                claimed[v] += 1
                owner.setdefault(v, []).append(name)

        for value, count in claimed.items():
            if count > 1:
                out.append(Finding(
                    code="AMBIGUOUS_CONDITION", severity="critical", field=fld.name,
                    claim=(f"value {value!r} is claimed by {count} different "
                           f"condition names; which one is meant cannot be settled "
                           f"from the copybook or the data"),
                    evidence={"value": value, "names": ", ".join(owner[value])},
                    records=st.examined if st else 0))

        if st:
            uncovered = {v.strip(): c for v, c in st.distinct.items()
                         if v.strip() and v.strip() not in claimed}
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
