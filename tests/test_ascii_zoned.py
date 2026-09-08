"""The ASCII-NATIVE zoned sign, which is a third convention, not a variant.

Three different things end a signed DISPLAY field, and only the code page says
which:

    mainframe EBCDIC        -123.45 -> 0xF1 F2 F3 F4 D5
    that file, translated   -123.45 -> '1' '2' '3' '4' 'N'
    PC COBOL, ASCII-native  -123.45 -> 0x31 32 33 34 75   ('1234u')

The tool handled the first two and fell through the third. The consequence was
not an error: the sign was lost AND the final digit dropped, so -123.45 read as
12.34 with nothing reported - a plausible wrong number, which is the failure the
whole project exists to catch. It was found by a reader of the LinkedIn post,
not by this suite, so the suite gets the case.

Every rule here is exercised twice: against data carrying the fault, and against
data identical apart from it. A check that fires on both is not a detector.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from overpunch.copybook import parse
from overpunch.decode import (ascii_zone_sign, decode_display,
                              encode_ascii_zoned, encode_overpunch_bytes,
                              is_ascii_page)
from overpunch.findings import evaluate
from overpunch.layout import Picture
from overpunch.probe import scan

CB = """\
000100 01  MF-REC.
000200     05  MF-REF     PIC X(04).
000300     05  MF-AMOUNT  PIC S9(03)V99.
"""
UNSIGNED_CB = CB.replace("PIC S9(03)V99", "PIC 9(03)V99 ")
PIC = Picture(raw="S9(03)V99", is_numeric=True, digits=5, scale=2, signed=True)


def rec(value: str, negative: bool) -> bytes:
    return b"REF1" + encode_ascii_zoned(value.rjust(5, "0"), negative)


def codes(tmp_path, records: list[bytes], copybook=CB, encoding="latin-1"):
    path = tmp_path / "d.dat"
    path.write_bytes(b"".join(records))
    layout = parse(copybook)
    return {f.code for f in evaluate(
        layout, scan(str(path), layout, encoding=encoding))}


# --- the planted fault -----------------------------------------------------

def test_the_byte_that_used_to_be_dropped():
    """0x75 is 'u'. Before the fix this returned 12.34: sign gone, digit gone."""
    raw = bytes([0x31, 0x32, 0x33, 0x34, 0x75])
    assert decode_display(raw, PIC, "latin-1") == Decimal("-123.45")


def test_the_positive_form_is_an_ordinary_digit():
    raw = bytes([0x31, 0x32, 0x33, 0x34, 0x35])
    assert decode_display(raw, PIC, "latin-1") == Decimal("123.45")


@pytest.mark.parametrize("digit", range(10))
def test_every_negative_digit_round_trips(digit):
    encoded = encode_ascii_zoned(f"1234{digit}", negative=True)
    assert encoded[-1] == 0x70 + digit
    assert decode_display(encoded, PIC, "latin-1") == Decimal(f"-1234{digit}") / 100


def test_scan_reports_the_sign_it_can_now_see(tmp_path):
    found = codes(tmp_path, [rec("12345", True)] * 3 + [rec("12345", False)])
    assert "TRAILING_SIGN" in found


def test_it_stays_quiet_when_nothing_is_negative(tmp_path):
    """The discrimination half. Same layout, same convention, no negatives."""
    found = codes(tmp_path, [rec("12345", False)] * 4)
    assert "TRAILING_SIGN" not in found
    assert "SIGN_PRESENT_ALL_POSITIVE" not in found, (
        "a plain ASCII digit is not evidence of a sign; treating it as one "
        "reports every unsigned ASCII column as signed")


def test_an_unsigned_pic_carrying_the_convention_is_a_finding(tmp_path):
    assert "UNDECLARED_SIGN" in codes(
        tmp_path, [rec("12345", True)] * 4, copybook=UNSIGNED_CB)


# --- the convention must not leak onto EBCDIC ------------------------------

def test_ebcdic_is_not_read_with_the_ascii_convention(tmp_path):
    """0x75 is not a sign on an EBCDIC page, and inventing one would be worse.

    The same bytes, declared cp037. The tool must refuse to read them rather
    than report 60 negatives that are not there.
    """
    found = codes(tmp_path, [rec("12345", True)] * 4, encoding="cp037")
    assert "UNKNOWN_SIGN_BYTE" in found
    assert "TRAILING_SIGN" not in found


def test_real_ebcdic_still_reads_the_zone_nibble(tmp_path):
    """The other half: the fix must not have cost the case that worked."""
    recs = [b"REF1" + encode_overpunch_bytes("12345", True, "cp037")] * 3 + \
           [b"REF1" + encode_overpunch_bytes("12345", False, "cp037")]
    found = codes(tmp_path, recs, encoding="cp037")
    assert "TRAILING_SIGN" in found
    assert "UNKNOWN_SIGN_BYTE" not in found


def test_a_translated_ebcdic_file_still_reads_as_before(tmp_path):
    """'N' is 0x4E - outside 0x70-0x79, so the two conventions cannot collide."""
    recs = [b"REF1" + b"1234N"] * 3 + [b"REF1" + b"1234E"]
    assert "TRAILING_SIGN" in codes(tmp_path, recs)


# --- the unknown byte fails closed -----------------------------------------

def test_junk_in_the_final_byte_is_reported(tmp_path):
    recs = [b"REF1" + b"1234" + bytes([0x2A])] * 4      # '*'
    assert "UNKNOWN_SIGN_BYTE" in codes(tmp_path, recs)


def test_padding_is_not_junk(tmp_path):
    """Low-values and trailing spaces are how a mainframe says 'unset'.

    An earlier rule counted every non-digit final byte as a sign and reported
    each empty one-digit indicator as a copybook error. This one must not
    repeat that in a new colour.
    """
    for pad in (b"\x00", b" ", b"\xff"):
        recs = [b"REF1" + b"1234" + pad] * 4
        assert "UNKNOWN_SIGN_BYTE" not in codes(tmp_path, recs), pad


def test_a_clean_file_raises_nothing(tmp_path):
    recs = [b"REF1" + b"12345"] * 4
    assert "UNKNOWN_SIGN_BYTE" not in codes(tmp_path, recs)


# --- the code-page test itself ---------------------------------------------

@pytest.mark.parametrize("page,expected", [
    ("latin-1", True), ("cp1252", True), ("utf-8", True), ("ascii", True),
    ("cp037", False), ("cp273", False), ("cp500", False), ("cp1026", False),
    ("no-such-codec", False)])
def test_is_ascii_page(page, expected):
    assert is_ascii_page(page) is expected


def test_ascii_zone_sign_returns_none_for_everything_else():
    for byte in (0x00, 0x20, 0x2D, 0x2B, 0x4A, 0x7B, 0x6F, 0xD5, 0xFF):
        assert ascii_zone_sign(bytes([byte])) is None, hex(byte)
