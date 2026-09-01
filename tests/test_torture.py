"""The hardest record I could write, and the arithmetic it has to reproduce.

Every construct here is one a real copybook throws at a decoder: packed decimal
with an odd digit count, three widths of binary, IBM hexadecimal floats that
carry no PICTURE at all, a repeating group, a variable repeating group, and two
REDEFINES competing for the same twenty bytes.

The expected record length is computed BY HAND in the docstring below, not taken
from the parser. A test that asks the code what the answer is and then agrees
with it proves nothing.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from overpunch.copybook import CopybookError, parse, parse_file
from overpunch.decode import decode_hex_float, encode_hex_float
from overpunch.layout import LayoutError, Usage

CPY = str(Path(__file__).resolve().parents[1] / "demo" / "TORTURE.cpy")

# TR-KEY              2 + 10                       = 12
# TR-SIGNED-DISPLAY   S9(07)V99  -> 9 digits       =  9
# TR-SIGN-LEADING     S9(05) LEADING SEPARATE 5+1  =  6
# TR-SIGN-TRAIL-SEP   S9(05) TRAILING SEPARATE 5+1 =  6
# TR-PACKED           S9(09)V99 COMP-3 11//2+1     =  6
# TR-PACKED-ODD       S9(04)    COMP-3  4//2+1     =  3
# TR-BIN-HALF         S9(04) COMP                  =  2
# TR-BIN-FULL         S9(09) COMP                  =  4
# TR-BIN-DOUBLE       S9(18) COMP                  =  8
# TR-UNSIGNED-BIN     9(04)  COMP                  =  2
# TR-FLOAT-SHORT      COMP-1                       =  4
# TR-FLOAT-LONG       COMP-2                       =  8
# TR-QUARTERS         (4 + 1) x 4                  = 20
# TR-ENTRY-COUNT      S9(03) COMP-3                =  2
# TR-ENTRIES          (3 + 5) x 6                  = 48
# TR-PAYLOAD          X(20), both REDEFINES add 0  = 20
# TR-DISCRIMINATOR    X(01)                        =  1
#                                              total 161
EXPECTED_LENGTH = 161

EXPECTED_OFFSETS = {
    "TR-REGION": 0, "TR-ACCOUNT": 2, "TR-SIGNED-DISPLAY": 12,
    "TR-SIGN-LEADING": 21, "TR-SIGN-TRAIL-SEP": 27, "TR-PACKED": 33,
    "TR-PACKED-ODD": 39, "TR-BIN-HALF": 42, "TR-BIN-FULL": 44,
    "TR-BIN-DOUBLE": 48, "TR-UNSIGNED-BIN": 56, "TR-FLOAT-SHORT": 58,
    "TR-FLOAT-LONG": 62, "TR-Q-AMT": 70, "TR-Q-FLAG": 74,
    "TR-ENTRY-COUNT": 90, "TR-E-CODE": 92, "TR-E-VALUE": 95,
    "TR-PAYLOAD": 140, "TR-DISCRIMINATOR": 160,
}


@pytest.fixture(scope="module")
def layout():
    return parse_file(CPY)


def test_record_length_matches_the_hand_computed_total(layout):
    assert layout.record_length() == EXPECTED_LENGTH


def test_every_field_lands_where_it_was_computed_to(layout):
    got = {f.name: f.offset for f in layout.elementary_fields()}
    for name, off in EXPECTED_OFFSETS.items():
        assert got.get(name) == off, f"{name} at {got.get(name)}, expected {off}"


def test_floats_have_no_picture_and_must_still_be_sized(layout):
    """COMP-1 and COMP-2 are declared with a USAGE and no PICTURE.

    They sized to zero and were dropped from the field list entirely, which
    shortened the record by 12 bytes and moved every field after them.
    """
    for name, width in (("TR-FLOAT-SHORT", 4), ("TR-FLOAT-LONG", 8)):
        f = layout.find(name)
        assert f.pic is None
        assert f.total_size() == width
        assert f in layout.elementary_fields()


def test_a_repeating_group_steps_over_all_its_occurrences(layout):
    """TR-QUARTERS OCCURS 4 is 5 bytes each. The next field must be 20 on."""
    assert layout.find("TR-QUARTERS").total_size() == 20
    assert layout.find("TR-ENTRY-COUNT").offset == layout.find("TR-Q-AMT").offset + 20


def test_a_variable_repeating_group_reserves_its_maximum(layout):
    """OCCURS 1 TO 6 DEPENDING ON reserves 6 x 8 bytes."""
    entries = layout.find("TR-ENTRIES")
    assert entries.occurs == 6
    assert entries.occurs_depending_on == "TR-ENTRY-COUNT"
    assert layout.find("TR-PAYLOAD").offset == layout.find("TR-E-CODE").offset + 48


def test_both_redefines_share_the_bytes_they_redefine(layout):
    base = layout.find("TR-PAYLOAD").offset
    assert layout.find("TR-P-SURNAME").offset == base
    assert layout.find("TR-P-NUMBER").offset == base
    assert layout.find("TR-P-EXPIRY").offset == base + 10


def test_the_discriminator_names_which_redefines_branch_is_live(layout):
    d = layout.find("TR-DISCRIMINATOR")
    assert d.conditions == {"PAYLOAD-IS-PERSON": ["P"], "PAYLOAD-IS-POLICY": ["L"]}


def test_packed_with_an_odd_digit_count_still_rounds_up(layout):
    assert layout.find("TR-PACKED-ODD").total_size() == 3


def test_binary_widths_follow_the_digit_count(layout):
    assert layout.find("TR-BIN-HALF").total_size() == 2
    assert layout.find("TR-BIN-FULL").total_size() == 4
    assert layout.find("TR-BIN-DOUBLE").total_size() == 8


# --- IBM hexadecimal float --------------------------------------------------

def test_ibm_hex_float_matches_the_documented_encoding():
    """0x41100000 is 1.0 in IBM HFP. It is 9.0 read as IEEE 754."""
    assert decode_hex_float(bytes.fromhex("41100000")) == 1
    assert decode_hex_float(bytes.fromhex("C1100000")) == -1
    assert decode_hex_float(bytes.fromhex("42640000")) == 100
    assert decode_hex_float(bytes(4)) == 0


def test_ibm_hex_float_is_not_ieee():
    """The reason this decoder exists rather than a struct.unpack call."""
    import struct
    raw = bytes.fromhex("41100000")
    assert decode_hex_float(raw) == 1
    assert struct.unpack(">f", raw)[0] == 9.0


@pytest.mark.parametrize("value", ["1", "-1", "100", "0.5", "3.5", "-1234.5"])
def test_hex_float_round_trips(value):
    back = decode_hex_float(encode_hex_float(Decimal(value), 4))
    assert abs(back - Decimal(value)) < Decimal("0.001")


# --- the guard that catches a corrupt parse ---------------------------------

def test_a_statement_that_lost_its_period_is_refused_not_guessed():
    """A line running past column 72 loses its terminating period.

    The statement then swallows the next line, the group inherits a PICTURE from
    whatever it ate, and the record length comes out wrong with nothing said.
    That is how this copybook first parsed: 131 bytes instead of 161.
    """
    corrupt = (
        "000100 01  REC.\n"
        "000200     05  GRP OCCURS 1 TO 6 TIMES DEPENDING ON SOME-VERY-LONG-COUNT-NAME.\n"
        "000300         10  SUB   PIC X(03).\n"
    )
    assert len(corrupt.splitlines()[1]) > 72, "fixture must exceed column 72"
    with pytest.raises((CopybookError, LayoutError)) as exc:
        parse(corrupt)
    assert "column 72" in str(exc.value)


def test_a_long_line_that_keeps_its_period_is_allowed():
    """Overlong is only fatal when the terminator is what gets discarded."""
    ok = ("000100 01  REC.\n"
          "000200     05  A  PIC X(03).                                       IDENT001\n")
    assert len(ok.splitlines()[1]) > 72
    parse(ok)


def test_a_well_formed_copybook_passes_the_same_guard():
    parse("000100 01  REC.\n000200     05  A  PIC X(03).\n")


# --- OCCURS on an elementary field, not just on a group ---------------------

def test_occurs_on_an_elementary_field_advances_once_per_occurrence():
    """`PIC X(40) OCCURS 5` is 200 bytes, and the next field starts 200 on.

    The offset walk returned total_size() for a leaf - which already includes
    OCCURS - and the caller multiplied again, advancing 1000 bytes. Every
    OCCURS in the torture record is on a GROUP, so this path was never taken
    until it met real copybooks that put OCCURS on a field.
    """
    layout = parse(
        "000100 01  REC.\n"
        "000200     05  BEFORE     PIC X(02).\n"
        "000300     05  LINE-ITEM  PIC X(40) OCCURS 5 TIMES.\n"
        "000400     05  AFTER      PIC X(03).\n")
    assert layout.find("LINE-ITEM").total_size() == 200
    assert layout.find("AFTER").offset == 2 + 200
    assert layout.record_length() == 2 + 200 + 3


def test_a_group_and_an_elementary_occurs_agree_on_length():
    """The same repetition expressed both ways must measure the same."""
    grouped = parse("000100 01  R.\n"
                    "000200     05  G OCCURS 4 TIMES.\n"
                    "000300         10  V   PIC X(06).\n")
    flat = parse("000100 01  R.\n"
                 "000200     05  V   PIC X(06) OCCURS 4 TIMES.\n")
    assert grouped.record_length() == flat.record_length() == 24
