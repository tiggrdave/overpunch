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
    """A declared scale with no decimal point stored anywhere.

    This was restricted to DISPLAY fields, which quietly exempted packed and
    binary money - the exact same trap. A COMP-3 decoder that returns the
    integer and leaves scaling to the caller is the common case, and the caller
    routinely forgets. The corpus caught this: samples/packed-heavy is a file of
    signed, scaled COMP-3 money about which the tool had nothing to say.
    """
    return bool(fld.pic and fld.pic.is_numeric and fld.pic.scale)


def width_underfill(fld: Field, st: FieldStats) -> bool:
    """A declared width the data never reaches.

    Only meaningful for unscaled fields. Money is declared wide on purpose, so
    firing on a scaled field is noise rather than a find.
    """
    return bool(fld.pic and fld.pic.is_numeric and fld.pic.scale == 0
                and st.max_significant_digits
                and st.max_significant_digits <= fld.pic.int_digits - 1)


def unknown_sign_byte(fld: Field, st: FieldStats) -> bool:
    """A numeric DISPLAY field ending in a byte that is none of the known things.

    Not a digit, not any sign convention the decoder implements, and not the
    padding an unset field carries. The value cannot be read without knowing
    which convention wrote it, and reading it as digits drops the last one - so
    this fails closed rather than returning a plausible number.
    """
    return bool(fld.pic and fld.pic.is_numeric and fld.usage is Usage.DISPLAY
                and st.unknown_sign_byte)


def invalid_packed(fld: Field, st: FieldStats) -> bool:
    return bool(st.invalid_packed)


def never_populated(fld: Field, st: FieldStats) -> bool:
    """Empty in every record - blank, zero, or low-values.

    Low-values had to be added: `blank` counts whitespace, and str.strip() does
    not remove 0x00, so a column that a mainframe had left entirely unset
    produced no finding at all. On a real file two coded fields were empty in
    all 703 records and the tool said nothing about either.
    """
    return bool(st.examined and (st.blank == st.examined or
                                 st.all_zero == st.examined or
                                 st.unset == st.examined))


def populated_filler(fld: Field, st: FieldStats) -> bool:
    """FILLER that carries a real value.

    `blank` only tests for whitespace, and a mainframe writes low-values into
    space it is not using. A FILLER full of 0x00 is reserved space behaving
    exactly as intended, not an undocumented field.
    """
    return bool(fld.is_filler and st.examined and st.unset < st.examined)


def duplicated_condition_values(fld: Field) -> list[str]:
    """Values that two conditions each claim as their ONLY value.

    A condition enumerating the permitted set beside conditions naming each
    member of it is ordinary COBOL, not an ambiguity:

        88  CLASS-VALID      VALUE 'F' 'N'.
        88  CLASS-FRAUD      VALUE 'F'.
        88  CLASS-NON-FRAUD  VALUE 'N'.

    Flagging that reported nine critical findings on one real file and every one
    was noise. What is genuinely unresolvable is two names for the same single
    value, where nothing says which was meant.
    """
    singles = Counter()
    for name, values in fld.conditions.items():
        if len(values) == 1 and not fld.condition_ranges.get(name):
            singles[values[0]] += 1
    return [v for v, n in singles.items() if n > 1]


def _in_any_range(value: str, fld: Field) -> bool:
    for spans in fld.condition_ranges.values():
        for lo, hi in spans:
            if lo <= value <= hi:
                return True
            try:
                if float(lo) <= float(value) <= float(hi):
                    return True
            except ValueError:
                pass
    return False


def is_unset(text: str) -> bool:
    """Low-values and spaces are how a mainframe says "no value".

    They are not an undeclared code, and reporting them as one buried a real
    observation - this column is empty in 690 of 703 records - inside a finding
    about condition coverage.
    """
    return all(c in ("\x00", " ", "\xff") for c in text) if text else True


def unset_share(fld: Field, st: FieldStats) -> tuple[int, int]:
    """(records where the field is unset, records examined)."""
    return st.unset, st.examined


def uncovered_values(fld: Field, st: FieldStats) -> dict[str, int]:
    if not fld.conditions and not fld.condition_ranges:
        return {}
    claimed = {v for vals in fld.conditions.values() for v in vals}
    return {v.strip(): c for v, c in st.distinct.items()
            if not is_unset(v) and v.strip() not in claimed
            and not _in_any_range(v.strip(), fld)}
