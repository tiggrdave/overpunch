"""The single definition of each rule's condition.

`scan` and `explain` are two callers asking the same questions of the same
measurements. When each carried its own copy of the condition they drifted:
`scan` had learned to stop nagging about headroom on money fields and `explain`
had not, so the same file produced "no finding" from one command and "CONFIRMED"
from the other. Anything both paths test lives here, once.
"""

from __future__ import annotations

from collections import Counter

from .layout import Field, Usage
from .probe import FieldStats


def signed_bytes_present(fld: Field, st: FieldStats) -> bool:
    """The final byte carries a sign rather than a digit, in at least one record."""
    return bool(fld.pic and fld.pic.is_numeric and fld.usage is Usage.DISPLAY
                and (st.overpunch_negative + st.overpunch_positive))


def sign_is_load_bearing(fld: Field, st: FieldStats) -> bool:
    """A declared sign that actually changes a value in THIS file.

    A signed field whose every record is positive is not a trap yet. Reporting
    it as one is how a detector turns into noise.
    """
    return bool(fld.pic and fld.pic.signed and st.overpunch_negative)


def sign_present_but_inert(fld: Field, st: FieldStats) -> bool:
    return bool(fld.pic and fld.pic.signed and not st.overpunch_negative
                and st.overpunch_positive)


def undeclared_sign(fld: Field, st: FieldStats) -> bool:
    return bool(fld.pic and fld.pic.is_numeric and not fld.pic.signed
                and signed_bytes_present(fld, st))


def implied_decimal(fld: Field, st: FieldStats) -> bool:
    return bool(fld.pic and fld.pic.is_numeric and fld.pic.scale
                and fld.usage is Usage.DISPLAY)


def width_underfill(fld: Field, st: FieldStats) -> bool:
    """A declared width the data never reaches.

    Only meaningful for unscaled fields. Money is declared wide on purpose, so
    firing on a scaled field is noise rather than a find.
    """
    return bool(fld.pic and fld.pic.is_numeric and fld.pic.scale == 0
                and st.max_significant_digits
                and st.max_significant_digits <= fld.pic.int_digits - 1)


def invalid_packed(fld: Field, st: FieldStats) -> bool:
    return bool(st.invalid_packed)


def never_populated(fld: Field, st: FieldStats) -> bool:
    return bool(st.examined and (st.blank == st.examined or
                                 st.all_zero == st.examined))


def populated_filler(fld: Field, st: FieldStats) -> bool:
    return bool(fld.is_filler and st.examined and st.blank < st.examined)


def duplicated_condition_values(fld: Field) -> list[str]:
    counts = Counter(v for vals in fld.conditions.values() for v in vals)
    return [v for v, n in counts.items() if n > 1]


def uncovered_values(fld: Field, st: FieldStats) -> dict[str, int]:
    if not fld.conditions:
        return {}
    claimed = {v for vals in fld.conditions.values() for v in vals}
    return {v.strip(): c for v, c in st.distinct.items()
            if v.strip() and v.strip() not in claimed}
