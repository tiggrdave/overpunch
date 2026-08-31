"""Evidence gathering over the actual bytes.

Nothing in this module guesses. Every finding it emits carries the count of
records it was measured over, so a reader can tell the difference between
"observed in 96,412 of 100,000 records" and "the model thought so".
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field as dc_field
from decimal import Decimal

from .decode import (DecodeError, decode_display, decode_display_naive,
                     decode_packed, decode_text, split_overpunch)
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


def iter_records(path: str, record_length: int):
    """Yield fixed-length records. Raises if the file is not a whole multiple."""
    with open(path, "rb") as fh:
        data = fh.read()
    if record_length <= 0:
        raise ValueError("record length must be positive")
    remainder = len(data) % record_length
    if remainder:
        raise LayoutMismatch(len(data), record_length, remainder)
    for i in range(0, len(data), record_length):
        yield data[i:i + record_length]


class LayoutMismatch(Exception):
    def __init__(self, filesize: int, record_length: int, remainder: int):
        self.filesize, self.record_length, self.remainder = filesize, record_length, remainder
        super().__init__(
            f"file of {filesize} bytes is not a whole multiple of the copybook's "
            f"{record_length}-byte record ({remainder} bytes left over)")


def scan(path: str, layout: Layout, encoding: str = "cp037",
         limit: int | None = None) -> dict[str, FieldStats]:
    fields = layout.elementary_fields()
    stats = {f.name: FieldStats(name=f.name) for f in fields}
    rlen = layout.record_length()

    for n, rec in enumerate(iter_records(path, rlen)):
        if limit is not None and n >= limit:
            break
        for fld in fields:
            raw = rec[fld.offset:fld.offset + fld.total_size()]
            _observe(stats[fld.name], fld, raw, encoding)
    return stats


def _observe(st: FieldStats, fld: Field, raw: bytes, encoding: str) -> None:
    st.examined += 1
    pic = fld.pic
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
        return

    last = text[-1:] if text else ""
    if last and not last.isdigit():
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
            digits_, sign_ = split_overpunch(text_)
            digits_ = "".join(c for c in digits_ if c.isdigit()) or "0"
            forced = Decimal(digits_) * sign_
            st.sum_forced_signed += forced.scaleb(-pic.scale) if pic.scale else forced
    except (DecodeError, ArithmeticError):
        pass
