"""Which float format wrote a COMP-1/COMP-2 field, decided by counting.

Four or eight bytes of floating point are either IBM hexadecimal float or IEEE
754, and NOTHING in the bytes records which. This was the one place the tool
asserted a default instead of measuring, and its own docstring admitted it.

The wrong reading does not fail. 161,916.39 written as IEEE and read as IBM hex
float comes back as 505,354,496.00 - finite, ordinary-looking, three thousand
times too large. So no single value can settle it, and "does this look
plausible" is not a check; it is a judgement that would never fire.

The column settles it. IBM HFP keeps its fraction normalised, so the leading
hex digit - the high nibble of byte 1 - is never zero for a non-zero value. In
IEEE that nibble is the bottom of the exponent and the top of the mantissa, and
it is zero a measurable share of the time. Measured over 4,000 values:

    true IBM HFP   binary32  0.00%     binary64   0.00%
    true IEEE 754  binary32  4.35%     binary64  26.15%

One such value refutes HFP outright. None of them, over enough values, refutes
IEEE. Below the floor the honest answer is "cannot tell", and this suite
requires the tool to say so rather than confirm its default.
"""

from __future__ import annotations

import struct
from decimal import Decimal
from pathlib import Path

import pytest

from overpunch.copybook import parse
from overpunch.decode import (FLOAT_SAMPLE_FLOOR, DecodeError, decode_float,
                              decode_hex_float, decode_ieee_float,
                              encode_hex_float, encode_ieee_float,
                              is_unnormalised_hfp)
from overpunch.findings import evaluate
from overpunch.probe import scan

CB = """\
000100 01  FLOAT-REC.
000200     05  FR-ID      PIC 9(06).
000300     05  FR-SINGLE  COMP-1.
000400     05  FR-DOUBLE  COMP-2.
"""
# Deliberately spread over several decades. A column confined to one binade
# CANNOT settle the question - see test_a_narrow_column_is_undecidable - so
# fixtures that pretend otherwise would be testing a rule the tool does not have.
_RNG = __import__("random").Random(5)
VALUES = [round(_RNG.uniform(0.01, 500000), 2) for _ in range(200)]
NARROW = [round(1000 + i * 0.5, 2) for i in range(200)]   # all one binade


def build(tmp_path, encoder, n, name="f.dat"):
    out = bytearray()
    for i in range(n):
        v = Decimal(str(VALUES[i % len(VALUES)]))
        out += f"{700000 + i:06d}".encode("cp037")
        out += encoder(v, 4) + encoder(v, 8)
    path = tmp_path / name
    path.write_bytes(bytes(out))
    return str(path)


def codes(tmp_path, encoder, n=120, fmt="hfp"):
    layout = parse(CB)
    path = build(tmp_path, encoder, n)
    return {f.code for f in evaluate(
        layout, scan(path, layout, float_format=fmt))}


# --- the value the wrong reading returns -----------------------------------

def test_the_wrong_reading_returns_an_ordinary_number():
    """This is why nothing else can catch it: no error, no absurdity."""
    v = Decimal("161916.39")
    as_ieee = encode_ieee_float(v, 4)
    assert decode_ieee_float(as_ieee) == Decimal("161916.390625")
    misread = decode_hex_float(as_ieee)
    assert misread == Decimal("505354496")
    assert misread.is_finite() and misread > 0


@pytest.mark.parametrize("width", [4, 8])
def test_round_trip_both_formats(width):
    v = Decimal("1234.5")
    assert decode_hex_float(encode_hex_float(v, width)) == v
    assert decode_ieee_float(encode_ieee_float(v, width)) == v


# --- the discriminator, on its own -----------------------------------------

@pytest.mark.parametrize("width", [4, 8])
def test_normalised_hex_float_is_never_unnormalisable(width):
    assert not any(is_unnormalised_hfp(encode_hex_float(Decimal(str(v)), width))
                   for v in VALUES)


@pytest.mark.parametrize("width,floor", [(4, 0.02), (8, 0.10)])
def test_ieee_bytes_do_show_the_tell(width, floor):
    hits = sum(is_unnormalised_hfp(encode_ieee_float(Decimal(str(v)), width))
               for v in VALUES)
    assert hits / len(VALUES) >= floor, (
        f"only {hits}/{len(VALUES)}; the rule needs this rate to exist")


def test_zero_is_zero_in_both_formats_and_is_not_evidence():
    assert not is_unnormalised_hfp(bytes(4))
    assert not is_unnormalised_hfp(bytes(8))


def test_ieee_nonfinite_is_refused_not_returned():
    for raw in (struct.pack(">f", float("inf")), struct.pack(">f", float("nan"))):
        with pytest.raises(DecodeError):
            decode_ieee_float(raw)


# --- the planted fault, and its discrimination half ------------------------

def test_ieee_data_read_as_hex_float_is_caught(tmp_path):
    assert "FLOAT_FORMAT_MISMATCH" in codes(tmp_path, encode_ieee_float)


def test_hex_float_data_read_as_hex_float_is_not_flagged(tmp_path):
    found = codes(tmp_path, encode_hex_float)
    assert "FLOAT_FORMAT_MISMATCH" not in found
    assert "FLOAT_FORMAT_CONFIRMED" in found, (
        "a measurement that says nothing when it succeeds is indistinguishable "
        "from the assertion it replaced")


def test_hex_float_data_read_as_ieee_is_caught(tmp_path):
    """The other direction. The same statistic, read the other way."""
    assert "FLOAT_FORMAT_MISMATCH" in codes(tmp_path, encode_hex_float, fmt="ieee")


def test_ieee_data_read_as_ieee_is_not_flagged(tmp_path):
    found = codes(tmp_path, encode_ieee_float, fmt="ieee")
    assert "FLOAT_FORMAT_MISMATCH" not in found
    assert "FLOAT_FORMAT_CONFIRMED" in found


# --- the honest third state ------------------------------------------------

def test_too_few_values_is_undecidable_not_confirmed(tmp_path):
    """Absence of evidence, below the floor, is not evidence.

    A short HFP column and a short IEEE column can look identical. The tool has
    to say it cannot tell rather than confirm whichever default it started with.
    """
    found = codes(tmp_path, encode_hex_float, n=FLOAT_SAMPLE_FLOOR - 1)
    assert "FLOAT_FORMAT_UNDECIDABLE" in found
    assert "FLOAT_FORMAT_CONFIRMED" not in found
    assert "FLOAT_FORMAT_MISMATCH" not in found


def test_the_floor_is_where_it_says_it_is(tmp_path):
    assert "FLOAT_FORMAT_CONFIRMED" in codes(
        tmp_path, encode_hex_float, n=FLOAT_SAMPLE_FLOOR)


def test_a_short_ieee_column_is_still_caught_when_the_tell_appears(tmp_path):
    """The floor gates CONFIRMED, never MISMATCH: one tell is proof, not a rate."""
    found = codes(tmp_path, encode_ieee_float, n=FLOAT_SAMPLE_FLOOR - 1)
    assert "FLOAT_FORMAT_MISMATCH" in found


def test_a_narrow_column_is_undecidable_in_either_format(tmp_path):
    """Measured: 200 values inside one binade give 0 tells whichever wrote them.

    All the values share one exponent byte, so the value that would refute hex
    float never had a chance to appear. Confirming a format here would be the
    original assertion wearing a measurement's clothes.
    """
    def narrow(encoder):
        layout = parse(CB)
        out = bytearray()
        for i, v in enumerate(NARROW):
            out += f"{700000 + i:06d}".encode("cp037")
            out += encoder(Decimal(str(v)), 4) + encoder(Decimal(str(v)), 8)
        path = tmp_path / f"n{encoder.__name__}.dat"
        path.write_bytes(bytes(out))
        return {f.code for f in evaluate(layout, scan(str(path), layout))}

    for encoder in (encode_hex_float, encode_ieee_float):
        found = narrow(encoder)
        assert "FLOAT_FORMAT_UNDECIDABLE" in found, encoder.__name__
        assert "FLOAT_FORMAT_CONFIRMED" not in found, encoder.__name__


def test_an_all_zero_column_says_nothing_either_way(tmp_path):
    layout = parse(CB)
    path = tmp_path / "z.dat"
    path.write_bytes(b"".join(f"{700000+i:06d}".encode("cp037") + bytes(12)
                              for i in range(200)))
    found = {f.code for f in evaluate(layout, scan(str(path), layout))}
    for code in ("FLOAT_FORMAT_MISMATCH", "FLOAT_FORMAT_CONFIRMED",
                 "FLOAT_FORMAT_UNDECIDABLE"):
        assert code not in found, code


def test_decode_float_dispatches(tmp_path):
    v = Decimal("1234.5")
    assert decode_float(encode_hex_float(v, 4), "hfp") == v
    assert decode_float(encode_ieee_float(v, 4), "ieee") == v
