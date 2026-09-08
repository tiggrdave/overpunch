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

# one C-level pass instead of a Python call per character
_ONLY_DIGITS = str.maketrans("", "", "".join(
    chr(c) for c in range(256) if not chr(c).isdigit()))


def _digits(text: str) -> str:
    return text.translate(_ONLY_DIGITS)


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


def zone_sign(raw: bytes) -> tuple[int, str] | None:
    """Recover the sign and final digit from the ZONE NIBBLE of the last byte.

    Every EBCDIC code page agrees that 0xC_ is positive, 0xD_ is negative and
    0xF_ is unsigned, with the digit in the low nibble. The CHARACTER those
    bytes decode to does not agree at all: 0xD0 is '}' in cp037, 'ü' in cp273
    (German) and 'ğ' in cp1026 (Turkish).

    Reading the sign from decoded text therefore works only on US code pages.
    On German data the -0 overpunch is not recognised, the digit is dropped and
    the record silently turns positive - measured on a 200-record file as a
    122,228 difference in the total, reported with no warning whatsoever.

    Returns None when the last byte is not an EBCDIC signed-numeric zone, so
    ASCII data with a trailing '+'/'-' falls through to the character path.
    """
    if not raw:
        return None
    zone, digit = raw[-1] >> 4, raw[-1] & 0x0F
    if zone in (0x0C, 0x0D) and digit <= 9:
        return (-1 if zone == 0x0D else 1), str(digit)
    return None


_ASCII_PAGE: dict[str, bool] = {}


def is_ascii_page(encoding: str) -> bool:
    """True when this code page writes digits as 0x30-0x39.

    Asked of the codec rather than matched against a list of names, so latin-1,
    cp1252, utf-8 and every alias of them answer correctly and no maintenance is
    required when a new one turns up.
    """
    hit = _ASCII_PAGE.get(encoding)
    if hit is None:
        try:
            hit = "0".encode(encoding) == b"\x30"
        except LookupError:
            hit = False
        _ASCII_PAGE[encoding] = hit
    return hit


def ascii_zone_sign(raw: bytes) -> tuple[int, str] | None:
    """Recover the sign from an ASCII-NATIVE zoned decimal's last byte.

    Micro Focus, ACUCOBOL and the other PC COBOLs never translate from EBCDIC.
    They write ASCII digits and fold the sign in by setting 0x40 in the zone, so
    -123.45 ends in 0x75 ('u') - not the 0xD5 a mainframe writes, and not the
    '}' that an EBCDIC file leaves behind once translated. Three conventions,
    and only the code page says which is in play, which is why this is selected
    by `is_ascii_page` rather than tried opportunistically: on EBCDIC data 0x75
    is not a sign at all and guessing that it is would invent negatives.

    Before this existed the byte fell through every branch: the sign was lost
    AND the last digit dropped, so -123.45 read as 12.34 with nothing reported -
    the exact failure this project exists to catch, inside this project.

    The POSITIVE form is a plain digit byte and therefore indistinguishable from
    an unsigned field. It is returned for the value, but `probe` counts only the
    negative form as a sign byte; counting the positive one would report every
    ASCII numeric column as carrying a sign.

    Returns None for anything else - including the rarer ASCII conventions - so
    an unrecognised final byte becomes an UNKNOWN_SIGN_BYTE finding rather than
    a quiet guess.
    """
    if not raw:
        return None
    byte = raw[-1]
    if 0x70 <= byte <= 0x79:
        return -1, str(byte - 0x70)
    if 0x30 <= byte <= 0x39:
        return 1, str(byte - 0x30)
    return None


def encode_ascii_zoned(digits: str, negative: bool) -> bytes:
    """Inverse of ascii_zone_sign, for building fixtures."""
    return digits[:-1].encode("ascii") + bytes([(0x70 if negative else 0x30)
                                                | int(digits[-1])])


def encode_overpunch_bytes(digits: str, negative: bool, encoding: str = "cp037") -> bytes:
    """Build a signed DISPLAY field the way a mainframe writes one: by byte.

    The leading digits are the code page's own digit bytes; the final byte
    carries the sign in its zone nibble.
    """
    head = digits[:-1].encode(encoding)
    zone = 0xD0 if negative else 0xC0
    return head + bytes([zone | int(digits[-1])])


def encode_overpunch(digits: str, negative: bool) -> str:
    """Inverse of split_overpunch, for building fixtures."""
    table = _OVERPUNCH_NEG if negative else _OVERPUNCH_POS
    return digits[:-1] + table[int(digits[-1])]


def decode_display(raw: bytes, pic: Picture, encoding: str = "cp037",
                   sign_position: SignPosition = SignPosition.TRAILING,
                   sign_separate: bool = False) -> Decimal:
    """Correct DISPLAY numeric read: honours the sign AND the implied decimal.

    The overpunch is recovered whether or not the copybook declares the field
    signed, because the zone nibble carries the sign while the LOW nibble still
    carries a digit. Skipping that recovery for a `PIC 9` field silently drops
    the last digit and divides the value by ten - which is exactly what this
    function used to do, on the one column in the demo file that is declared
    unsigned and signed in practice.

    The sign itself is only applied when it was declared. A field whose bytes
    disagree with its PIC is a finding (UNDECLARED_SIGN), not something to
    quietly reinterpret.
    """
    text = decode_text(raw, encoding).strip()
    sign = 1

    if pic.signed and sign_separate:
        if sign_position is SignPosition.LEADING:
            sign = -1 if text[:1] == "-" else 1
            text = text[1:]
        else:
            sign = -1 if text[-1:] == "-" else 1
            text = text[:-1]
    elif sign_position is SignPosition.LEADING and not sign_separate:
        head, observed = split_overpunch(text[:1])
        text = head + text[1:]
        if pic.signed:
            sign = observed
    else:
        # the byte's zone nibble is the same in every EBCDIC page; the character
        # it decodes to is not, so read the sign from the byte where possible
        zoned = zone_sign(raw)
        if zoned is None and is_ascii_page(encoding):
            # an ASCII-native zoned field: the sign is 0x40 in the zone, and no
            # EBCDIC page can reach this branch, so the two cannot collide
            zoned = ascii_zone_sign(raw)
        if zoned is not None:
            observed, last_digit = zoned
            text = text[:-1] + last_digit
        else:
            text, observed = split_overpunch(text)
        if pic.signed:
            sign = observed

    digits = _digits(text) or "0"
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
    return Decimal(_digits(text) or "0")


def decode_packed(raw: bytes, scale: int = 0) -> Decimal:
    """COMP-3 packed decimal: two digits per byte, sign in the final low nibble.

    Every digit nibble must be 0-9 and the final low nibble must be a sign
    (0xA-0xF; 0xF is how an unsigned COMP-3 field is written). Anything else is
    not packed decimal and raises, because the alternative is worse than an
    error: str() on a nibble of 0xF emits "15" straight into the digit string,
    so three junk bytes used to return 1,515,151,515 - a confident number, from
    bytes that are not a number at all. That is the precise failure this project
    exists to catch, and it was reachable through `overpunch decode`.

    `probe` validates the nibbles before calling this, so the scan path never
    reaches the raise; `decode` had no such guard and wrote the corruption into
    the extract.
    """
    if not raw:
        return Decimal(0)
    digits = []
    last = len(raw) - 1
    for i, byte in enumerate(raw):
        hi, lo = byte >> 4, byte & 0x0F
        if hi > 9:
            raise DecodeError(
                f"byte {i} of {len(raw)} has digit nibble 0x{hi:X}: not packed decimal")
        digits.append(str(hi))
        if i == last:
            if lo < 0x0A:
                raise DecodeError(
                    f"final low nibble 0x{lo:X} is a digit, not a sign: "
                    f"not packed decimal")
            sign = -1 if lo in (0x0B, 0x0D) else 1
        else:
            if lo > 9:
                raise DecodeError(
                    f"byte {i} of {len(raw)} has digit nibble 0x{lo:X}: "
                    f"not packed decimal")
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


def decode_hex_float(raw: bytes) -> Decimal:
    """IBM hexadecimal floating point - COMP-1 (4 bytes) and COMP-2 (8 bytes).

    This is NOT IEEE 754. One sign bit, a 7-bit exponent biased by 64, and a
    fraction interpreted in base SIXTEEN: value = -1^s * 0.F * 16^(E-64).
    Reading these bytes with struct.unpack('>f') returns a plausible number that
    is simply wrong, which is the reason this function exists rather than a
    one-line call.

    (A compiler option can emit IEEE instead. Legacy extracts are overwhelmingly
    hexadecimal, so that is the default here; nothing in the bytes distinguishes
    them, which is worth knowing before trusting either reading.)
    """
    if len(raw) not in (4, 8):
        raise DecodeError(f"hex float must be 4 or 8 bytes, got {len(raw)}")
    sign = -1 if raw[0] & 0x80 else 1
    exponent = (raw[0] & 0x7F) - 64
    fraction_bits = int.from_bytes(raw[1:], "big")
    if fraction_bits == 0:
        return Decimal(0)
    fraction = Decimal(fraction_bits) / Decimal(1 << (8 * (len(raw) - 1)))
    return sign * fraction * (Decimal(16) ** exponent)


def encode_hex_float(value: Decimal, width: int = 4) -> bytes:
    """Inverse of decode_hex_float, for building fixtures."""
    if value == 0:
        return bytes(width)
    negative = value < 0
    v = abs(Decimal(value))
    exponent = 0
    while v >= 1:
        v /= 16
        exponent += 1
    while v < Decimal("0.0625"):
        v *= 16
        exponent -= 1
    fraction_bits = int(v * (1 << (8 * (width - 1))))
    head = (0x80 if negative else 0) | ((exponent + 64) & 0x7F)
    return bytes([head]) + fraction_bits.to_bytes(width - 1, "big")


# How many non-zero values it takes before "no unnormalised value was seen" is
# evidence rather than a small sample. Measured on 4,000 values: reading true
# IEEE bytes as HFP leaves 4.35% of binary32 and 26.15% of binary64 values with
# a zero leading fraction nibble, which normalised HFP cannot produce. At the
# binary32 rate, 0.9565^67 < 0.05 - so 67 clean values is 95% confidence, and
# binary64 reaches that far sooner. Below the floor the honest answer is "cannot
# tell", and the rule says so rather than confirming the default.
FLOAT_SAMPLE_FLOOR = 67

# ...and how many distinct magnitudes the column must span before the ABSENCE of
# a tell means anything. A column whose values all sit inside one binade shares a
# single exponent byte, and then neither format produces a tell - measured over
# 200 values, both widths, both formats:
#
#   spread over decades   exponent bytes seen: 2-5   tells: 0 (hfp) / 3-47 (ieee)
#   all inside one binade exponent bytes seen: 1     tells: 0 (hfp) / 0    (ieee)
#
# In that second row a rule keyed only on "no tell seen" CONFIRMS whichever format
# it started with, on evidence that could not have contradicted it - the assertion
# this rule was written to replace, wearing a measurement's clothes. One exponent
# byte is the exact signature of that case, and every decidable column measured
# has at least two.
FLOAT_EXPONENT_SPREAD = 2


def hfp_exponent_byte(raw: bytes) -> int:
    """The magnitude byte, sign stripped. Its VARIETY is what makes a tell possible."""
    return (raw[0] & 0x7F) if raw else 0


def hfp_leading_nibble(raw: bytes) -> int:
    """The nibble the whole discriminator turns on: high half of byte 1."""
    return (raw[1] >> 4) if len(raw) >= 2 else 0


def decode_ieee_float(raw: bytes) -> Decimal:
    """IEEE 754 binary32/binary64, big-endian - the OTHER thing 4 or 8 bytes may be.

    A compiler option emits these instead of IBM hexadecimal float, and nothing
    in the bytes says which. See `float_format_evidence` for how the column as a
    whole settles it.
    """
    import struct
    if len(raw) not in (4, 8):
        raise DecodeError(f"IEEE float must be 4 or 8 bytes, got {len(raw)}")
    value = struct.unpack(">f" if len(raw) == 4 else ">d", raw)[0]
    if value != value or value in (float("inf"), float("-inf")):
        raise DecodeError("not a finite IEEE value")
    return Decimal(repr(value))


def encode_ieee_float(value: Decimal, width: int = 4) -> bytes:
    """Inverse of decode_ieee_float, for building fixtures."""
    import struct
    return struct.pack(">f" if width == 4 else ">d", float(value))


def decode_float(raw: bytes, float_format: str = "hfp") -> Decimal:
    return (decode_ieee_float(raw) if float_format == "ieee"
            else decode_hex_float(raw))


def is_unnormalised_hfp(raw: bytes) -> bool:
    """True when these bytes cannot be a normalised IBM hex float.

    IBM HFP keeps the fraction normalised: the leading hex digit is non-zero for
    every non-zero value, because that is what normalising means. That digit is
    the high nibble of the second byte. In IEEE it is not a fraction digit at
    all - it is the bottom of the exponent and the top of the mantissa - so it
    is zero a measurable fraction of the time.

    One such value in a column is therefore proof the bytes are not HFP. Zero of
    them, over enough values, is evidence they are. This is the whole
    discriminator, and it is a count, not a judgement about plausibility: an
    IEEE column read as HFP returns confident, finite, ordinary-looking numbers
    - 161,916.39 comes back as 505,354,496.00 - so nothing else about the value
    gives it away.
    """
    if len(raw) < 2 or not any(raw):        # all-zero is zero in both formats
        return False
    return (raw[1] >> 4) == 0


def decode_binary(raw: bytes, signed: bool = True) -> int:
    """COMP / COMP-4: big-endian, two's complement when signed."""
    return int.from_bytes(raw, byteorder="big", signed=signed)


def decode_field(raw: bytes, fld: Field, encoding: str = "cp037",
                 float_format: str = "hfp"):
    if fld.usage in (Usage.COMP1, Usage.COMP2):
        return decode_float(raw, float_format)
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
