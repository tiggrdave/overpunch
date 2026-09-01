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

from .decode import (DecodeError, decode_binary, decode_display,
                     decode_display_naive, decode_hex_float,
                     decode_packed, decode_text, split_overpunch,
                     zone_sign)
from .layout import Field, Layout, Usage

SEVERITY = ("info", "warn", "critical")


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
    all_zero: int = 0
    nondigit_last_byte: int = 0
    overpunch_negative: int = 0
    overpunch_positive: int = 0
    ascii_signed: int = 0
    max_significant_digits: int = 0
    distinct: Counter = dc_field(default_factory=Counter)
    undecodable: int = 0
    invalid_packed: int = 0
    sum_correct: Decimal = Decimal(0)
    sum_abs: Decimal = Decimal(0)      # sign ignored, implied decimal honoured
    sum_naive: Decimal = Decimal(0)    # sign AND implied decimal ignored
    sum_forced_signed: Decimal = Decimal(0)  # what an unsigned field WOULD total


def detect_recfm(path: str, record_length: int) -> str:
    """Fixed, or variable-length with a record descriptor word?

    A VB record carries a 4-byte RDW: a big-endian length that INCLUDES the RDW
    itself, then two zero bytes. So the test is not arithmetic alone - the first
    RDW has to read as a sane length, and its reserved bytes have to be zero.
    Guessing from divisibility would call any file whose size happens to divide
    by record_length + 4 variable-length.
    """
    size = os.path.getsize(path)
    if record_length > 0 and size % record_length == 0:
        return "fixed"
    with open(path, "rb") as fh:
        head = fh.read(4)
    if len(head) == 4:
        declared = int.from_bytes(head[:2], "big")
        if head[2:] == b"\x00\x00" and 4 < declared <= 32767:
            if declared - 4 == record_length or size % declared == 0:
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
            if declared < 4 or rdw[2:] != b"\x00\x00":
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


def explain_mismatch(filesize: int, record_length: int) -> list[str]:
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
    return notes


class LayoutMismatch(Exception):
    def __init__(self, filesize: int, record_length: int, remainder: int):
        self.filesize, self.record_length, self.remainder = filesize, record_length, remainder
        self.notes = explain_mismatch(filesize, record_length)
        super().__init__(
            f"file of {filesize} bytes is not a whole multiple of the copybook's "
            f"{record_length}-byte record ({remainder} bytes left over)"
            + "".join(f"\n  - {n}" for n in self.notes))


def scan(path: str, layout: Layout, encoding: str = "cp037",
         limit: int | None = None, recfm: str = "auto") -> dict[str, FieldStats]:
    fields = layout.elementary_fields()
    stats = {f.name: FieldStats(name=f.name) for f in fields}
    rlen = layout.record_length()

    for n, rec in enumerate(iter_records(path, rlen, recfm=recfm)):
        if limit is not None and n >= limit:
            break
        for fld in fields:
            raw = rec[fld.offset:fld.offset + fld.total_size()]
            _observe(stats[fld.name], fld, raw, encoding)
    return stats


def _observe(st: FieldStats, fld: Field, raw: bytes, encoding: str) -> None:
    st.examined += 1

    # COMP-1 and COMP-2 are declared with a USAGE and no PICTURE at all. Every
    # data file until the torture record happened to have neither, so scan()
    # crashed on the first copybook that did.
    if fld.usage in (Usage.COMP1, Usage.COMP2):
        try:
            st.sum_correct += decode_hex_float(raw)
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
        return

    if fld.usage is Usage.COMP3:
        body = raw[:-1] if raw else b""
        if any((b >> 4) > 9 or (b & 0x0F) > 9 for b in body):
            st.invalid_packed += 1
        elif raw and (raw[-1] & 0x0F) not in (0x0C, 0x0D, 0x0F):
            st.invalid_packed += 1
        else:
            st.sum_correct += decode_packed(raw, pic.scale)
        return
    if fld.usage is Usage.COMP:
        value = Decimal(decode_binary(raw, signed=pic.signed))
        st.sum_correct += value.scaleb(-pic.scale) if pic.scale else value
        return

    # the sign is in the byte's zone nibble, which every EBCDIC page shares -
    # not in the character it decodes to, which they do not agree on
    zoned = zone_sign(raw)
    last = text[-1:] if text else ""
    if zoned is not None:
        st.nondigit_last_byte += 1
        if zoned[0] < 0:
            st.overpunch_negative += 1
        else:
            st.overpunch_positive += 1
    elif last and not last.isdigit():
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

    if zoned is not None:
        digits = text[:-1] + zoned[1]
    else:
        digits, _ = split_overpunch(text)
    digits = "".join(c for c in digits if c.isdigit())
    if not text.strip():
        st.blank += 1
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
            if z is not None:
                digits_, sign_ = text_[:-1] + z[1], z[0]
            else:
                digits_, sign_ = split_overpunch(text_)
            digits_ = "".join(c for c in digits_ if c.isdigit()) or "0"
            forced = Decimal(digits_) * sign_
            st.sum_forced_signed += forced.scaleb(-pic.scale) if pic.scale else forced
    except (DecodeError, ArithmeticError):
        pass
