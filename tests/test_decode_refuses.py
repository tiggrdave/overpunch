"""The decoder must fail closed, and `decode` must not outrun `scan`.

Two faults, one shape. `decode_packed` turned a nibble of 0xF into the digits
"15", so three junk bytes returned 1,515,151,515 - no error, a confident number
from bytes that are not a number. And `overpunch decode` never called `scan` at
all, so the corruption reached the extract with the checks sitting unused in the
same repository: one command called the file INVALID_PACKED while the other
wrote 121122123124125.12 out of it.

Neither was caught by the suite, and both were reachable from the command line.
The reason is the shape of the harness, not the rules: every existing test
reaches `decode_packed` THROUGH `probe`, which validates the nibbles first, so
nothing ever handed the decoder garbage. These tests hand it garbage.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from overpunch import cli
from overpunch.decode import DecodeError, decode_packed, encode_packed

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "samples" / "data"

needs_corpus = pytest.mark.skipif(
    not DATA.exists(),
    reason="run python samples/build_samples.py to generate the corpus")


# --- the planted fault -----------------------------------------------------

@pytest.mark.parametrize("raw,why", [
    (b"\xff\xff\xfc", "every nibble is 0xF; this returned 1,515,151,515"),
    (b"ABC", "ASCII text read as packed; this returned 41,424"),
    (bytes([0x1A, 0x2C]), "0xA is not a digit"),
    (bytes([0x12, 0x3E, 0x4C]), "0xE in a digit position"),
    (bytes([0x12, 0x34, 0x53]), "final low nibble is a digit, not a sign"),
    (bytes([0x12, 0x34, 0x50]), "0x0 is not a sign nibble"),
])
def test_packed_decimal_that_is_not_packed_decimal_raises(raw, why):
    with pytest.raises(DecodeError):
        decode_packed(raw, 2)


# --- the discrimination half -----------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (bytes([0x12, 0x34, 0x5C]), "123.45"),    # C: positive
    (bytes([0x12, 0x34, 0x5D]), "-123.45"),   # D: negative
    (bytes([0x12, 0x34, 0x5F]), "123.45"),    # F: unsigned
    (bytes([0x12, 0x34, 0x5A]), "123.45"),    # A: the rarer positive
    (bytes([0x12, 0x34, 0x5B]), "-123.45"),   # B: the rarer negative
    (bytes([0x00, 0x01, 0x2F]), "0.12"),
])
def test_real_packed_decimal_still_decodes(raw, expected):
    """A refusal that also refuses valid data is not a check, it is a bug."""
    assert decode_packed(raw, 2) == Decimal(expected)


@pytest.mark.parametrize("value", ["0", "1", "-1", "99999999.99", "-40000.05"])
def test_round_trip_through_the_encoder(value):
    assert decode_packed(encode_packed(Decimal(value), 11, 2), 2) == Decimal(value)


def test_empty_is_zero_not_an_error():
    assert decode_packed(b"", 0) == Decimal(0)


# --- decode must not outrun scan -------------------------------------------

@needs_corpus
def test_decode_refuses_a_file_scan_calls_critical(tmp_path, capsys):
    out = tmp_path / "out.csv"
    rc = cli.main(["decode", str(DATA / "corrupt-packed.cpy"),
                   str(DATA / "corrupt-packed.dat"), "--out", str(out)])
    assert rc == 2
    assert not out.exists(), "the extract was written despite a critical finding"
    printed = capsys.readouterr().out
    assert "INVALID_PACKED" in printed
    assert "--force" in printed, "a refusal has to say how to override it"


@needs_corpus
def test_force_writes_but_leaves_the_bad_values_empty(tmp_path, capsys):
    """--force is an override on the FINDING, not a licence to invent numbers.

    The unreadable cells stay empty and are counted out loud. Before this, the
    same bytes produced 121122123124125.12.
    """
    out = tmp_path / "out.csv"
    rc = cli.main(["decode", str(DATA / "corrupt-packed.cpy"),
                   str(DATA / "corrupt-packed.dat"), "--out", str(out),
                   "--force", "--limit", "8"])
    assert rc == 0 and out.exists()
    printed = capsys.readouterr().out
    assert "--force given" in printed
    assert "left EMPTY" in printed
    body = out.read_text()
    assert ",," in body, "the unreadable value should be an empty cell"
    assert "121122123124125" not in body


@needs_corpus
def test_a_clean_file_is_not_obstructed(tmp_path):
    """The discrimination half: the gate must not stand in front of good data."""
    out = tmp_path / "out.csv"
    rc = cli.main(["decode", str(DATA / "clean.cpy"), str(DATA / "clean.dat"),
                   "--out", str(out)])
    assert rc == 0 and out.exists()
    assert len(out.read_text().strip().splitlines()) == 51   # header + 50


@needs_corpus
def test_a_layout_mismatch_is_not_forceable(tmp_path, capsys):
    """There are no records to decode, only an offset that happens to be arithmetic."""
    out = tmp_path / "out.csv"
    rc = cli.main(["decode", str(DATA / "wrong-copybook.cpy"),
                   str(DATA / "wrong-copybook.dat"), "--out", str(out), "--force"])
    assert rc == 2
    assert not out.exists()
    assert "LAYOUT_MISMATCH" in capsys.readouterr().out
