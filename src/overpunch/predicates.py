"""The single definition of each rule's condition.

`scan` and `explain` are two callers asking the same questions of the same
measurements. When each carried its own copy of the condition they drifted:
`scan` had learned to stop nagging about headroom on money fields and `explain`
had not, so the same file produced "no finding" from one command and "CONFIRMED"
from the other. Anything both paths test lives here, once.
"""

from __future__ import annotations

from collections import Counter

from .decode import (BYTEORDER_SPREAD, FLOAT_BYTEORDER_RATIO,
                     FLOAT_EXPONENT_SPREAD, FLOAT_SAMPLE_FLOOR,
                     binary_pic_limit)
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


def float_evidence(fld: Field, st: FieldStats) -> str | None:
    """What the BYTES say a COMP-1/COMP-2 column is, independent of the reading.

    "ieee"  - at least one value a normalised hex float cannot produce. Proof;
              one is enough, and no sample floor applies to it.
    "hfp"   - no such value, over enough values, spanning enough magnitudes
              that one would have shown up. Evidence, not proof.
    None    - the column cannot settle it, which is a real answer and the one
              this rule exists to be able to give.
    """
    if fld.usage not in (Usage.COMP1, Usage.COMP2) or not st.float_nonzero:
        return None
    if st.float_unnormalised:
        return "ieee"
    if (st.float_nonzero >= FLOAT_SAMPLE_FLOOR
            and len(st.float_exponents) >= FLOAT_EXPONENT_SPREAD):
        return "hfp"
    return None


def _byteorder_from_entropy(head: int, tail: int) -> str | None:
    """Which end holds the high-order bytes, from how much each end varies.

    The high-order end of a real column takes FEW distinct byte values, because
    business magnitudes cluster; the low-order end takes many. Measured:

        GnuCOBOL COMP-2 (x86, IEEE)   first 143   last   3   -> little
        GnuCOBOL COMP   (x86, S9(4))  first   2   last 200   -> big
        big-endian IEEE fixtures      first   2   last  25   -> big

    Returns None when neither end is clearly wider, which is the right answer
    for a column that genuinely uses its full range at both ends.
    """
    if max(head, tail) < BYTEORDER_SPREAD:
        return None
    if tail * FLOAT_BYTEORDER_RATIO <= head:
        return "little"
    if head * FLOAT_BYTEORDER_RATIO <= tail:
        return "big"
    return None


def float_byteorder(fld: Field, st: FieldStats) -> str | None:
    """"big" | "little" | None - which end of the field holds the exponent.

    Only meaningful once the format is known to be IEEE; hex float is a
    mainframe format and is always big-endian. Decided by entropy, not by
    plausibility: the exponent end of a real column takes few distinct byte
    values because business magnitudes cluster, while the low mantissa end takes
    many. Verified against a file a real compiler produced - GnuCOBOL on x86
    writes native order, and the tool called the format right and the byte order
    wrong, returning 1.16e-53 for 1.727 while reporting CONFIRMED.
    """
    return _byteorder_from_entropy(len(st.float_head_bytes),
                                   len(st.float_tail_bytes))


def float_reading(st: FieldStats) -> tuple[str, str | None]:
    """The reading in force, split into (format, byte order)."""
    if st.float_format == "ieee-le":
        return "ieee", "little"
    if st.float_format == "ieee":
        return "ieee", "big"
    return "hfp", None


def float_format_contradicted(fld: Field, st: FieldStats) -> bool:
    """The bytes disagree with the reading in force - on format, or on byte order.

    Byte order counts. Getting the format right and the order wrong is not a
    near miss: it is a different number, returned with no complaint.
    """
    ev = float_evidence(fld, st)
    if not ev:
        return False
    fmt, order = float_reading(st)
    if ev != fmt:
        return True
    if ev == "ieee":
        observed = float_byteorder(fld, st)
        return bool(observed and observed != order)
    return False


def float_format_confirmed(fld: Field, st: FieldStats) -> bool:
    """Both the format AND, for IEEE, the byte order are supported by the bytes."""
    ev = float_evidence(fld, st)
    fmt, order = float_reading(st)
    if ev != fmt:
        return False
    if ev == "ieee":
        return float_byteorder(fld, st) == order
    return True


def float_format_undecidable(fld: Field, st: FieldStats) -> bool:
    """Not enough values, or all one magnitude, for absence to be evidence."""
    if fld.usage not in (Usage.COMP1, Usage.COMP2) or not st.float_nonzero:
        return False
    ev = float_evidence(fld, st)
    if ev is None:
        return True
    # format settled, byte order not - still not a confirmation
    return ev == "ieee" and float_byteorder(fld, st) is None


def binary_byteorder(fld: Field, st: FieldStats) -> str | None:
    """Which end of a COMP/COMP-5 field holds the high-order bytes."""
    if fld.usage not in (Usage.COMP, Usage.COMP5) or not st.binary_nonzero:
        return None
    return _byteorder_from_entropy(len(st.binary_head_bytes),
                                   len(st.binary_tail_bytes))


def binary_byteorder_contradicted(fld: Field, st: FieldStats) -> bool:
    observed = binary_byteorder(fld, st)
    return bool(observed and observed != st.binary_order)


def binary_exceeds_pic(fld: Field, st: FieldStats) -> bool:
    """A COMP field holding more than its PICTURE allows is not standard COMP.

    Standard COMP truncates to the digit count: PIC S9(4) COMP holds at most
    9,999 though its two bytes reach 32,767. COMP-5 and TRUNC(BIN) use the full
    range. One value above the limit is proof - and unlike the byte-order test
    it needs no sample at all.

    Only asked of fields DECLARED COMP; a field declared COMP-5 is entitled to
    the full range. And not asked at all when the byte order is contradicted,
    because then the decoded values are not the field's values.
    """
    return bool(fld.usage is Usage.COMP and fld.pic and st.binary_over_pic
                and not binary_byteorder_contradicted(fld, st))


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
