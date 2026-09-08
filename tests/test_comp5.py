"""COMP-5 is not COMP, and the copybook is not always right about which.

Standard COMP truncates to the PICTURE: PIC S9(4) COMP holds at most 9,999
though its two bytes reach 32,767. COMP-5 - and any COMP compiled TRUNC(BIN) -
uses the full binary range, and is stored in the NATIVE byte order of whatever
machine wrote it. Neither fact is recorded in the file.

Two independent tells, and they need each other:

  the PICTURE  one value above 10**digits-1 is proof the field is not standard
               COMP. No sample size, no plausibility - arithmetic.
  the bytes    the high-order end of a real column varies little because
               magnitudes cluster. Read the wrong way round the values are
               still integers, just different ones, so nothing else notices.

The byte-order test has to run FIRST: read little-endian bytes as big-endian and
almost everything looks over-sized, which would fire the PICTURE rule for the
wrong reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from overpunch.copybook import parse
from overpunch.decode import binary_pic_limit, decode_binary
from overpunch.findings import evaluate
from overpunch.layout import Usage
from overpunch.probe import scan

CB = """\
000100 01  BIN-REC.
000200     05  BR-ID     PIC 9(06).
000300     05  BR-COUNT  PIC S9(04) COMP.
000400     05  BR-TOTAL  PIC S9(09) COMP.
"""
CB5 = CB.replace("PIC S9(04) COMP.", "PIC S9(04) COMP-5.")


def build(tmp_path, values, little=False, name="b.dat"):
    out = bytearray()
    order = "little" if little else "big"
    for i, v in enumerate(values):
        out += f"{800000 + i:06d}".encode("cp037")
        out += int(v).to_bytes(2, order, signed=True)
        out += int(i * 7919 % 900000).to_bytes(4, order, signed=True)
    path = tmp_path / name
    path.write_bytes(bytes(out))
    return str(path)


def codes(tmp_path, values, little=False, order="big", copybook=CB):
    layout = parse(copybook)
    path = build(tmp_path, values, little)
    return {f.code for f in evaluate(
        layout, scan(path, layout, binary_byteorder=order))}


# --- COMP-5 has to parse at all --------------------------------------------

@pytest.mark.parametrize("word", ["COMP-5", "COMPUTATIONAL-5", "COMP-X",
                                  "COMPUTATIONAL-X"])
def test_comp5_is_recognised_as_binary(word):
    """It used to fall through to DISPLAY: 4 bytes where the truth is 2.

    That does not mis-read one field. It shifts every field after it and
    changes the record length, so the copybook stops describing the file.
    """
    layout = parse(f"000100 01  R.\n000200     05  A  PIC S9(04) {word}.\n")
    field = layout.elementary_fields()[0]
    assert field.usage is Usage.COMP5
    assert field.total_size() == 2
    assert layout.record_length() == 2


def test_the_widths_match_comp():
    for digits, width in ((4, 2), (9, 4), (18, 8)):
        lay = parse(f"000100 01  R.\n000200     05  A  PIC S9({digits:02}) COMP-5.\n")
        assert lay.elementary_fields()[0].total_size() == width


# --- the PICTURE tell ------------------------------------------------------

def test_pic_limit():
    class P:
        digits = 4
    assert binary_pic_limit(P()) == 9999


def test_a_value_over_the_pic_limit_is_reported(tmp_path):
    assert "BINARY_EXCEEDS_PIC" in codes(tmp_path, [12000] * 40)


def test_one_value_is_enough(tmp_path):
    """Proof, not a rate: a standard COMP field cannot contain even one."""
    assert "BINARY_EXCEEDS_PIC" in codes(tmp_path, [10] * 39 + [10001])


def test_values_inside_the_pic_are_not_reported(tmp_path):
    """The discrimination half - and 9,999 itself is legal."""
    found = codes(tmp_path, list(range(9960, 10000)))
    assert "BINARY_EXCEEDS_PIC" not in found


def test_a_field_declared_comp5_may_use_its_whole_range(tmp_path):
    """Declared COMP-5, so the full range is not a contradiction."""
    assert "BINARY_EXCEEDS_PIC" not in codes(
        tmp_path, [12000] * 40, copybook=CB5)


# --- the byte-order tell ---------------------------------------------------

def test_little_endian_data_read_big_is_reported(tmp_path):
    assert "BINARY_BYTE_ORDER" in codes(
        tmp_path, list(range(1, 121)), little=True, order="big")


def test_little_endian_data_read_little_is_quiet(tmp_path):
    assert "BINARY_BYTE_ORDER" not in codes(
        tmp_path, list(range(1, 121)), little=True, order="little")


def test_big_endian_data_read_little_is_reported(tmp_path):
    """The rule must not simply always say 'little'."""
    assert "BINARY_BYTE_ORDER" in codes(
        tmp_path, list(range(1, 121)), little=False, order="little")


def test_big_endian_data_read_big_is_quiet(tmp_path):
    assert "BINARY_BYTE_ORDER" not in codes(
        tmp_path, list(range(1, 121)), little=False, order="big")


def test_too_few_records_decides_nothing(tmp_path):
    """Four records can give head=1, tail=4 and 'prove' anything."""
    found = codes(tmp_path, [1, 2, 3, 4], little=True, order="big")
    assert "BINARY_BYTE_ORDER" not in found


def test_the_picture_rule_defers_to_the_byte_order_rule(tmp_path):
    """Read the wrong way round, values look huge for the wrong reason.

    Reporting BINARY_EXCEEDS_PIC there would be a true statement about numbers
    that are not the field's values.
    """
    found = codes(tmp_path, list(range(1, 121)), little=True, order="big")
    assert "BINARY_BYTE_ORDER" in found
    assert "BINARY_EXCEEDS_PIC" not in found


def test_decode_binary_honours_byte_order():
    assert decode_binary(b"\x01\x00", signed=True) == 256
    assert decode_binary(b"\x01\x00", signed=True, little=True) == 1
