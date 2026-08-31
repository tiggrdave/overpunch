"""Byte-level decoders, and the naive counterparts that get them wrong.

Every trap this project reports is the difference between two functions in this
file. Keeping the wrong one as real, callable code is deliberate: it is what
lets a test *plant* the fault and require the detector to fire.
"""

from __future__ import annotations

from decimal import Decimal

from .layout import Field, Picture, SignPosition, Usage

# EBCDIC trailing-overpunch: the sign rides in the zone nibble of the last byte.
# 0xC_ positive, 0xD_ negative, 0xF_ unsigned.
_OVERPUNCH_POS = "{ABCDEFGHI"      # cp037 renderings of 0xC0..0xC9
_OVERPUNCH_NEG = "}JKLMNOPQR"      # cp037 renderings of 0xD0..0xD9

_ASCII_SIGN_CHARS = "+-"


class DecodeError(ValueError):
    pass


def decode_text(raw: bytes, encoding: str = "cp037") -> str:
    return raw.decode(encoding, errors="replace")


def split_overpunch(text: str) -> tuple[str, int]:
    """Return (digit string, sign) for a DISPLAY numeric already turned to text.

    Handles both the EBCDIC zone overpunch and the trailing +/- that survives a
    naive character-set conversion. Sign is +1 or -1.
    """
    if not text:
        return "", 1
    last = text[-1]
    if last in _ASCII_SIGN_CHARS:
        return text[:-1], (-1 if last == "-" else 1)
    idx = _OVERPUNCH_NEG.find(last)
    if idx >= 0:
        return text[:-1] + str(idx), -1
    idx = _OVERPUNCH_POS.find(last)
    if idx >= 0:
        return text[:-1] + str(idx), 1
    return text, 1


def encode_overpunch(digits: str, negative: bool) -> str:
    """Inverse of split_overpunch, for building fixtures."""
    table = _OVERPUNCH_NEG if negative else _OVERPUNCH_POS
    return digits[:-1] + table[int(digits[-1])]


def decode_display(raw: bytes, pic: Picture, encoding: str = "cp037",
                   sign_position: SignPosition = SignPosition.TRAILING,
                   sign_separate: bool = False) -> Decimal:
    """Correct DISPLAY numeric read: honours the sign AND the implied decimal."""
    text = decode_text(raw, encoding).strip()
    sign = 1
    if pic.signed:
        if sign_separate:
            if sign_position is SignPosition.LEADING:
                sign = -1 if text[:1] == "-" else 1
                text = text[1:]
            else:
                sign = -1 if text[-1:] == "-" else 1
                text = text[:-1]
        elif sign_position is SignPosition.LEADING:
            head, s = split_overpunch(text[0] + text[1:][::-1][:0] or text[0])
            sign = s
            text = head + text[1:]
        else:
            text, sign = split_overpunch(text)
    digits = "".join(c for c in text if c.isdigit()) or "0"
    value = Decimal(digits) * sign
    if pic.scale:
        value = value.scaleb(-pic.scale)
    return value


def decode_display_naive(raw: bytes, pic: Picture, encoding: str = "cp037") -> Decimal:
    """The read almost every ad-hoc extract script performs.

    Keeps only the characters that look like digits, ignores the sign entirely,
    and ignores the implied decimal. Present so the cost of the trap can be
    measured rather than asserted.
    """
    text = decode_text(raw, encoding)
    digits = "".join(c for c in text if c.isdigit()) or "0"
    return Decimal(digits)


def decode_packed(raw: bytes, scale: int = 0) -> Decimal:
    """COMP-3 packed decimal: two digits per byte, sign in the final low nibble."""
    digits = []
    for i, byte in enumerate(raw):
        hi, lo = byte >> 4, byte & 0x0F
        if i == len(raw) - 1:
            digits.append(str(hi))
            sign = -1 if lo in (0x0B, 0x0D) else 1
        else:
            digits.append(str(hi))
            digits.append(str(lo))
    value = Decimal("".join(digits) or "0") * sign
    return value.scaleb(-scale) if scale else value


def encode_packed(value: Decimal, digits: int, scale: int = 0) -> bytes:
    """Inverse of decode_packed, for building fixtures."""
    scaled = int((value.scaleb(scale)).to_integral_value())
    negative = scaled < 0
    text = str(abs(scaled)).rjust(digits, "0")[-digits:]
    if len(text) % 2 == 0:
        text = "0" + text
    out = bytearray()
    for i in range(0, len(text) - 1, 2):
        out.append((int(text[i]) << 4) | int(text[i + 1]))
    out.append((int(text[-1]) << 4) | (0x0D if negative else 0x0C))
    return bytes(out)


def decode_binary(raw: bytes, signed: bool = True) -> int:
    """COMP / COMP-4: big-endian, two's complement when signed."""
    return int.from_bytes(raw, byteorder="big", signed=signed)


def decode_field(raw: bytes, fld: Field, encoding: str = "cp037"):
    pic = fld.pic
    if pic is None:
        raise DecodeError(f"{fld.name} is a group item")
    if not pic.is_numeric:
        return decode_text(raw, encoding)
    if fld.usage is Usage.COMP3:
        return decode_packed(raw, pic.scale)
    if fld.usage is Usage.COMP:
        value = Decimal(decode_binary(raw, signed=pic.signed))
        return value.scaleb(-pic.scale) if pic.scale else value
    return decode_display(raw, pic, encoding, fld.sign_position, fld.sign_separate)
