"""Evidence gathering over the actual bytes.

Nothing in this module guesses. Every finding it emits carries the count of
records it was measured over, so a reader can tell the difference between
"observed in 96,412 of 100,000 records" and "the model thought so".
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field as dc_field
from decimal import Decimal

from .decode import (BYTEORDER_SPREAD, FLOAT_BYTEORDER_RATIO,
                     FLOAT_EXPONENT_SPREAD, FLOAT_SAMPLE_FLOOR, DecodeError,
                     binary_pic_limit,
                     ascii_zone_sign, decode_binary, decode_display,
                     decode_display_naive, decode_float, decode_packed,
                     decode_text, hfp_exponent_byte, is_ascii_page,
                     is_unnormalised_hfp, split_overpunch, zone_sign)
from .layout import Field, Layout, Usage

SEVERITY = ("info", "warn", "critical")

# Distinct values are recorded for character fields, and for NUMERIC fields that
# declare conditions - a coded field has few values by construction. Recording
# them for every numeric field would try to hold six million account numbers.
MAX_DISTINCT = 1000

# Only these can be a sign. A low-value byte, a space, or any other junk in the
# final position is an UNPOPULATED field, not a negative number - and counting
# it as a sign reported every empty one-digit indicator as a copybook error.
_SIGN_CHARS = set("{ABCDEFGHI}JKLMNOPQR+-")

# ...and these are how a mainframe says "no value", not a sign and not junk.
# Trailing spaces in particular are ordinary in a numeric field, so the
# unknown-byte rule below has to exempt them or it reports every one of them.
_PADDING_CHARS = set("\x00 \xff")


# Extracting digits with "".join(c for c in t if c.isdigit()) costs one Python
# call per CHARACTER. Profiling a 250 MB file showed 33 million isdigit() calls
# and 3.5 million joins - more than any other single line in the scan.
# str.translate does the same work in one pass inside C.
_ONLY_DIGITS = str.maketrans("", "", "".join(
    chr(c) for c in range(256) if not chr(c).isdigit()))


def _digits(text: str) -> str:
    """Every digit in the string, dropping everything else."""
    return text.translate(_ONLY_DIGITS)


def _is_unset(text: str) -> bool:
    """Low-values and spaces are how a mainframe says 'no value here'."""
    return all(c in ("\x00", " ", "\xff") for c in text) if text else True


class FieldStatsMap(dict):
    """Keyed by field, addressable by name where the name is unambiguous.

    Statistics have to be keyed per FIELD - FILLER occurs many times in one
    record and merging them doubled every count. But callers naturally write
    stats["W4-ACCOUNT"], so a bare name still resolves when exactly one field
    has it, and says so plainly when several do.
    """

    def __missing__(self, name: str):
        hits = [k for k in self if k.rsplit("@", 1)[0] == name]
        if len(hits) == 1:
            return self[hits[0]]
        if not hits:
            raise KeyError(name)
        raise KeyError(
            f"{name!r} occurs {len(hits)} times in this record; ask for one of "
            f"{', '.join(sorted(hits))}")


def field_key(fld: Field) -> str:
    """Unique per field. Names are not: FILLER appears many times in one record,
    and keying statistics by name merged them into a single entry with doubled
    counts and meaningless percentages."""
    return f"{fld.name}@{fld.offset}"


@dataclass
class Finding:
    code: str
    severity: str
    field: str
    claim: str
    evidence: dict = dc_field(default_factory=dict)
    records: int = 0
    impact: str | None = None

    def __str__(self) -> str:
        head = f"[{self.severity.upper():<8}] {self.code:<20} {self.field}"
        body = f"    {self.claim}"
        ev = "    evidence: " + ", ".join(f"{k}={v}" for k, v in self.evidence.items())
        out = [head, body, ev]
        if self.impact:
            out.append(f"    impact: {self.impact}")
        return "\n".join(out)


@dataclass
class FieldStats:
    name: str
    examined: int = 0
    blank: int = 0
    unset: int = 0        # low-values or spaces: 'no value'
    all_zero: int = 0
    nondigit_last_byte: int = 0
    overpunch_negative: int = 0
    overpunch_positive: int = 0
    ascii_signed: int = 0
    unknown_sign_byte: int = 0
    unknown_sign_values: Counter = dc_field(default_factory=Counter)
    float_nonzero: int = 0          # COMP-1/COMP-2 values that are not all-zero
    float_unnormalised: int = 0     # ...of those, how many HFP cannot produce
    float_exponents: set = dc_field(default_factory=set)  # magnitudes seen
    float_head_bytes: set = dc_field(default_factory=set)  # byte 0, all records
    float_tail_bytes: set = dc_field(default_factory=set)  # byte -1, all records
    binary_nonzero: int = 0
    binary_over_pic: int = 0        # values a standard COMP could not hold
    binary_max_abs: int = 0
    binary_head_bytes: set = dc_field(default_factory=set)
    binary_tail_bytes: set = dc_field(default_factory=set)
    binary_order: str = "big"       # the reading that was in force
    float_format: str = "hfp"       # the reading that was in force
    max_significant_digits: int = 0
    distinct: Counter = dc_field(default_factory=Counter)
    undecodable: int = 0
    invalid_packed: int = 0
    sum_correct: Decimal = Decimal(0)
    sum_abs: Decimal = Decimal(0)      # sign ignored, implied decimal honoured
    sum_naive: Decimal = Decimal(0)    # sign AND implied decimal ignored
    sum_forced_signed: Decimal = Decimal(0)  # what an unsigned field WOULD total


def _rdw_chain_fits(path: str, strict_reserved: bool = True,
                    max_records: int = 5_000_000) -> bool:
    """Walk the record-descriptor chain and see whether it lands on the end.

    This is the decisive test, and it is better than looking at the first
    descriptor: a length that happens to look plausible proves nothing, but a
    chain of them that consumes the file EXACTLY, to the byte, is not a
    coincidence. It also tolerates a file whose reserved bytes were not
    preserved in transit, which the first-descriptor test did not - and that is
    a real thing that happens to a dataset moved off a mainframe.
    """
    size = os.path.getsize(path)
    if size < 4:
        return False
    with open(path, "rb") as fh:
        at = 0
        for _ in range(max_records):
            rdw = fh.read(4)
            if not rdw:
                return at == size
            if len(rdw) < 4:
                return False
            declared = int.from_bytes(rdw[:2], "big")
            if declared < 4 or at + declared > size:
                return False
            if strict_reserved and rdw[2:] != b"\x00\x00":
                return False
            fh.seek(at + declared)
            at += declared
        return False


def detect_recfm(path: str, record_length: int) -> str:
    """Fixed, or variable-length with a record descriptor word?

    Divisibility decides nothing on its own: plenty of fixed files happen to
    divide by record_length + 4. What decides it is whether the descriptor chain
    consumes the file exactly.
    """
    size = os.path.getsize(path)
    if record_length > 0 and size % record_length == 0:
        return "fixed"
    for strict in (True, False):
        if _rdw_chain_fits(path, strict_reserved=strict):
            return "vb"
    return "fixed"


def _iter_vb(path: str, record_length: int):
    """Yield the body of each variable-length record, RDW stripped."""
    with open(path, "rb") as fh:
        offset = 0
        while True:
            rdw = fh.read(4)
            if not rdw:
                return
            if len(rdw) < 4:
                raise LayoutMismatch(os.path.getsize(path), record_length,
                                     len(rdw))
            declared = int.from_bytes(rdw[:2], "big")
            if declared < 4:
                raise VariableRecordError(
                    f"at byte {offset}: expected a record descriptor word, got "
                    f"{rdw.hex()}. Either this file is not RECFM=VB, or an "
                    f"earlier record's length was wrong and the reader is now "
                    f"out of step.")
            body = fh.read(declared - 4)
            if len(body) != declared - 4:
                raise VariableRecordError(
                    f"at byte {offset}: the record descriptor asks for "
                    f"{declared - 4} bytes and the file has {len(body)} left")
            offset += declared
            yield body


class VariableRecordError(Exception):
    pass


def iter_records(path: str, record_length: int, chunk_records: int = 4096,
                 recfm: str = "auto"):
    """Yield fixed-length records, streaming.

    This used to read the whole file into memory before yielding anything. On
    the demo fixtures that is invisible; on a real extract it is fatal, because
    the files this tool exists for are routinely gigabytes. Measured before the
    change: 20 MB of resident memory for a 21 MB file, growing linearly.

    The length check still happens up front, from the file size rather than its
    contents, so a copybook that does not divide the file is refused before a
    single record is decoded.
    """
    if record_length <= 0:
        raise ValueError("record length must be positive")
    if recfm == "auto":
        recfm = detect_recfm(path, record_length)
    if recfm == "vb":
        yield from _iter_vb(path, record_length)
        return

    size = os.path.getsize(path)
    remainder = size % record_length
    if remainder:
        raise LayoutMismatch(size, record_length, remainder)

    block = record_length * max(1, chunk_records)
    with open(path, "rb") as fh:
        while True:
            data = fh.read(block)
            if not data:
                return
            for i in range(0, len(data), record_length):
                yield data[i:i + record_length]


def divisors(size: int, low: int = 2, high: int = 8192) -> list[int]:
    """Every record length that would divide this file exactly."""
    out = []
    for n in range(low, min(high, size) + 1):
        if size % n == 0:
            out.append(n)
    return out


def describe_head(path: str, n: int = 8) -> str:
    """The first bytes, read as a descriptor word, for when detection fails.

    If a file will not divide and will not chain, the first eight bytes usually
    say why in one line: a sane descriptor means the chain derailed later, a
    wild one means this is not VB at all, and printable text means the transfer
    converted it.
    """
    with open(path, "rb") as fh:
        head = fh.read(n)
    if len(head) < 4:
        return f"file is only {len(head)} bytes"
    declared = int.from_bytes(head[:2], "big")
    reserved = head[2:4]
    hexed = " ".join(f"{b:02X}" for b in head)
    note = (f"first bytes {hexed} - as a descriptor word that is length "
            f"{declared}, reserved {reserved.hex()}")
    if reserved != b"\x00\x00":
        note += " (reserved bytes are not zero)"
    return note


def explain_mismatch(filesize: int, record_length: int,
                     path: str | None = None) -> list[str]:
    """Turn 'this does not divide' into a lead worth following.

    The file size is a hard constraint: only its divisors can be the record
    length. Saying which ones they are, and whether any is this copybook's
    record plus a header, is the difference between a dead end and a diagnosis.
    """
    notes = []
    exact = divisors(filesize)
    for extra, what in ((4, "a 4-byte record descriptor word (RECFM=VB)"),
                        (8, "a block and record descriptor word (RECFM=VBS)"),
                        (1, "a one-byte line terminator"),
                        (2, "a two-byte line terminator (CRLF)")):
        if filesize % (record_length + extra) == 0:
            notes.append(
                f"{record_length} + {extra} = {record_length + extra} divides it "
                f"exactly into {filesize // (record_length + extra):,} records, "
                f"which is this record plus {what}")
    plausible = [n for n in exact if 8 <= n <= 4096]
    if plausible:
        notes.append("record lengths that would divide this file exactly: "
                     + ", ".join(str(n) for n in plausible[:14])
                     + (" ..." if len(plausible) > 14 else ""))
    if not notes:
        notes.append("no plausible record length divides this file exactly; it "
                     "may carry a header, a trailer, or variable-length records")
    if path:
        try:
            notes.append(describe_head(path))
        except OSError:
            pass
    return notes


def partial_view_note(layout) -> str | None:
    """A copybook that redefines an area declared elsewhere covers only part."""
    external = layout.external_redefines()
    if not external:
        return None
    names = ", ".join(f"{a} REDEFINES {b}" for a, b in external)
    return (f"this copybook is a VIEW - {names} - so it may describe only the "
            f"leading {layout.described_length()} bytes of a longer record. If "
            f"one of the lengths above is the real record, pass it with "
            f"--record-bytes and the fields will be read within it.")


class LayoutMismatch(Exception):
    def __init__(self, filesize: int, record_length: int, remainder: int):
        self.filesize, self.record_length, self.remainder = filesize, record_length, remainder
        self.notes = explain_mismatch(filesize, record_length)
        super().__init__(
            f"file of {filesize} bytes is not a whole multiple of the copybook's "
            f"{record_length}-byte record ({remainder} bytes left over)"
            + "".join(f"\n  - {n}" for n in self.notes))


def scan(path: str, layout: Layout, encoding: str = "cp037",
         limit: int | None = None, recfm: str = "auto",
         float_format: str = "hfp",
         binary_byteorder: str = "big") -> dict[str, FieldStats]:
    fields = layout.elementary_fields()
    stats = FieldStatsMap((field_key(f), FieldStats(name=f.name)) for f in fields)
    rlen = layout.record_length()

    for n, rec in enumerate(iter_records(path, rlen, recfm=recfm)):
        if limit is not None and n >= limit:
            break
        for fld in fields:
            raw = rec[fld.offset:fld.offset + fld.total_size()]
            _observe(stats[field_key(fld)], fld, raw, encoding, float_format,
                     binary_byteorder)
    return stats


def _observe(st: FieldStats, fld: Field, raw: bytes, encoding: str,
             float_format: str = "hfp", binary_byteorder: str = "big") -> None:
    st.examined += 1

    # COMP-1 and COMP-2 are declared with a USAGE and no PICTURE at all. Every
    # data file until the torture record happened to have neither, so scan()
    # crashed on the first copybook that did.
    if fld.usage in (Usage.COMP1, Usage.COMP2):
        # Which of the two float formats these bytes are is not written down
        # anywhere, so it is measured: see decode.is_unnormalised_hfp. Counted
        # for every column whichever reading is in force, because the evidence
        # points the same way in both directions - a normalised HFP column has
        # zero of these and an IEEE one does not.
        if any(raw):
            st.float_nonzero += 1
            st.float_exponents.add(hfp_exponent_byte(raw))
            # the two ends, for byte order: the exponent end is the one with
            # few distinct values, because real magnitudes cluster
            st.float_head_bytes.add(raw[0])
            st.float_tail_bytes.add(raw[-1])
            if is_unnormalised_hfp(raw):
                st.float_unnormalised += 1
        st.float_format = float_format
        try:
            st.sum_correct += decode_float(raw, float_format)
        except (DecodeError, ArithmeticError):
            st.undecodable += 1
        return

    pic = fld.pic
    if pic is None:
        return
    try:
        text = decode_text(raw, encoding)
    except Exception:
        st.undecodable += 1
        return
    if "�" in text:
        st.undecodable += 1

    if not pic.is_numeric:
        st.distinct[text] += 1
        if not text.strip():
            st.blank += 1
        if _is_unset(text):
            st.unset += 1
        return

    # a numeric field can carry 88-levels too, and without its values the
    # uncovered-value rule could never fire for one
    if (fld.conditions or fld.condition_ranges) and len(st.distinct) < MAX_DISTINCT:
        st.distinct[text.strip()] += 1

    if fld.usage is Usage.COMP3:
        body = raw[:-1] if raw else b""
        if any((b >> 4) > 9 or (b & 0x0F) > 9 for b in body):
            st.invalid_packed += 1
        elif raw and (raw[-1] & 0x0F) not in (0x0C, 0x0D, 0x0F):
            st.invalid_packed += 1
        else:
            st.sum_correct += decode_packed(raw, pic.scale)
        return
    if fld.usage in (Usage.COMP, Usage.COMP5):
        # Two independent unknowns, measured separately. Byte order is not
        # recorded anywhere; and standard COMP truncates to the PICTURE while
        # COMP-5 does not, so a value above the PIC's limit is proof the field
        # is not what the copybook calls it.
        if raw and any(raw):
            st.binary_nonzero += 1
            st.binary_head_bytes.add(raw[0])
            st.binary_tail_bytes.add(raw[-1])
        st.binary_order = binary_byteorder
        integer = decode_binary(raw, signed=pic.signed,
                                little=(binary_byteorder == "little"))
        st.binary_max_abs = max(st.binary_max_abs, abs(integer))
        if abs(integer) > binary_pic_limit(pic):
            st.binary_over_pic += 1
        value = Decimal(integer)
        st.sum_correct += value.scaleb(-pic.scale) if pic.scale else value
        return

    # the sign is in the byte's zone nibble, which every EBCDIC page shares -
    # not in the character it decodes to, which they do not agree on
    zoned = zone_sign(raw)
    # an ASCII-native zoned field (Micro Focus and friends) folds the sign in as
    # 0x40 in the zone, so a negative ends in 0x70-0x79. Only the NEGATIVE form
    # counts as a sign byte: the positive one is a plain digit, and counting it
    # would report every ASCII numeric column as signed.
    if zoned is None and is_ascii_page(encoding):
        ascii_zoned = ascii_zone_sign(raw)
        if ascii_zoned is not None and ascii_zoned[0] < 0:
            zoned = ascii_zoned
    last = text[-1:] if text else ""
    if zoned is not None:
        st.nondigit_last_byte += 1
        if zoned[0] < 0:
            st.overpunch_negative += 1
        else:
            st.overpunch_positive += 1
    elif last and not last.isdigit() and last in _SIGN_CHARS:
        st.nondigit_last_byte += 1
        if last in "+-":
            st.ascii_signed += 1
            if last == "-":
                st.overpunch_negative += 1
            else:
                st.overpunch_positive += 1
        else:
            _, sign = split_overpunch(text)
            if sign < 0:
                st.overpunch_negative += 1
            else:
                st.overpunch_positive += 1
    elif last and not last.isdigit() and last not in _PADDING_CHARS:
        # neither a digit, nor any sign convention this tool knows, nor the
        # padding a mainframe leaves in an unset field. Reading it as digits
        # drops the last one silently, which is how the ASCII-native convention
        # went unnoticed here for the life of the project - so it is now a
        # finding rather than a fall-through.
        st.unknown_sign_byte += 1
        if len(st.unknown_sign_values) < 32:
            st.unknown_sign_values[f"0x{raw[-1]:02X}"] += 1

    if zoned is not None:
        digits = text[:-1] + zoned[1]
    else:
        digits, _ = split_overpunch(text)
    digits = _digits(digits)
    if not text.strip():
        st.blank += 1
    if _is_unset(text):
        st.unset += 1
    if digits and set(digits) == {"0"}:
        st.all_zero += 1
    st.max_significant_digits = max(st.max_significant_digits, len(digits.lstrip("0")))

    try:
        value = decode_display(raw, pic, encoding, fld.sign_position,
                               fld.sign_separate)
        st.sum_correct += value
        st.sum_abs += abs(value)
        st.sum_naive += decode_display_naive(raw, pic, encoding)
        if not pic.signed:
            text_ = decode_text(raw, encoding).strip()
            z = zone_sign(raw)
            if z is None and is_ascii_page(encoding):
                a = ascii_zone_sign(raw)
                z = a if (a is not None and a[0] < 0) else None
            if z is not None:
                digits_, sign_ = text_[:-1] + z[1], z[0]
            else:
                digits_, sign_ = split_overpunch(text_)
            digits_ = "".join(c for c in digits_ if c.isdigit()) or "0"
            forced = Decimal(digits_) * sign_
            st.sum_forced_signed += forced.scaleb(-pic.scale) if pic.scale else forced
    except (DecodeError, ArithmeticError):
        pass
