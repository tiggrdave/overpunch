"""Rehearsals, not assertions.

A check that has never failed proves it EXECUTES, not that it CATCHES. So every
rule here is exercised twice: once against a file carrying the fault it exists
for, and once against a file that is identical apart from the fault. A rule that
fires on both is not a detector, and this suite refuses it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from overpunch.copybook import parse
from overpunch.decode import encode_overpunch, encode_packed
from overpunch.findings import evaluate
from overpunch.probe import scan


def write(tmp_path, records: list[str], name="d.dat", encoding="cp037"):
    path = tmp_path / name
    path.write_bytes("".join(records).encode(encoding))
    return str(path)


def codes(tmp_path, copybook: str, records: list[str], encoding="cp037") -> set[str]:
    layout = parse(copybook)
    data = write(tmp_path, records, encoding=encoding)
    return {f.code for f in evaluate(layout, scan(data, layout, encoding=encoding))}


AMOUNT_CB = """
01  REC.
    05  AMT   PIC S9(06)V99.
"""
UNSIGNED_CB = """
01  REC.
    05  AMT   PIC 9(06)V99.
"""


def amt(value: str, negative: bool) -> str:
    return encode_overpunch(value.rjust(8, "0"), negative=negative)


# --- TRAILING_SIGN ---------------------------------------------------------

def test_trailing_sign_fires_when_negatives_are_present(tmp_path):
    recs = [amt("120050", negative=True)] * 3 + [amt("120050", negative=False)]
    assert "TRAILING_SIGN" in codes(tmp_path, AMOUNT_CB, recs)


def test_trailing_sign_stays_quiet_when_nothing_is_negative(tmp_path):
    """The discrimination test. Same field, same layout, no negatives."""
    recs = [amt("120050", negative=False)] * 4
    found = codes(tmp_path, AMOUNT_CB, recs)
    assert "TRAILING_SIGN" not in found
    assert "SIGN_PRESENT_ALL_POSITIVE" in found


def test_trailing_sign_impact_is_the_measured_difference(tmp_path):
    layout = parse(AMOUNT_CB)
    recs = [amt("100000", negative=True), amt("100000", negative=False)]
    data = write(tmp_path, recs)
    finding = next(f for f in evaluate(layout, scan(data, layout))
                   if f.code == "TRAILING_SIGN")
    # true total 0.00; sign ignored 2000.00
    assert "0.00" in finding.impact and "2,000.00" in finding.impact


# --- UNDECLARED_SIGN -------------------------------------------------------

def test_undeclared_sign_fires_when_pic9_carries_a_sign(tmp_path):
    recs = [amt("050000", negative=True)] + ["00050000"] * 3
    assert "UNDECLARED_SIGN" in codes(tmp_path, UNSIGNED_CB, recs)


def test_undeclared_sign_stays_quiet_on_genuinely_unsigned_data(tmp_path):
    assert "UNDECLARED_SIGN" not in codes(tmp_path, UNSIGNED_CB, ["00050000"] * 4)


# --- WIDTH_UNDERFILL -------------------------------------------------------

ID_CB = """
01  REC.
    05  EMP-NO   PIC 9(07).
"""


def test_width_underfill_fires_when_a_digit_is_never_used(tmp_path):
    assert "WIDTH_UNDERFILL" in codes(tmp_path, ID_CB, ["0123456", "0999999"])


def test_width_underfill_stays_quiet_when_the_width_is_used(tmp_path):
    assert "WIDTH_UNDERFILL" not in codes(tmp_path, ID_CB, ["1234567", "0123456"])


def test_width_underfill_does_not_nag_about_money_headroom(tmp_path):
    """Money fields are declared wide on purpose. Firing here is noise, not a find."""
    recs = [amt("000100", negative=False)] * 4
    assert "WIDTH_UNDERFILL" not in codes(tmp_path, AMOUNT_CB, recs)


# --- AMBIGUOUS_CONDITION / UNCOVERED_VALUE ---------------------------------

DUP_CB = """
01  REC.
    05  CODE-X   PIC X(01).
        88  IS-EXTENDED      VALUE 'R'.
        88  IS-REIMBURSABLE  VALUE 'R'.
        88  IS-STANDARD      VALUE 'S'.
"""
CLEAN_CB = """
01  REC.
    05  CODE-X   PIC X(01).
        88  IS-EXTENDED      VALUE 'R'.
        88  IS-STANDARD      VALUE 'S'.
"""


def test_ambiguous_condition_fires_on_a_duplicated_value(tmp_path):
    assert "AMBIGUOUS_CONDITION" in codes(tmp_path, DUP_CB, ["R", "S"])


def test_ambiguous_condition_stays_quiet_when_values_are_distinct(tmp_path):
    assert "AMBIGUOUS_CONDITION" not in codes(tmp_path, CLEAN_CB, ["R", "S"])


def test_uncovered_value_fires_on_a_code_no_88_level_claims(tmp_path):
    assert "UNCOVERED_VALUE" in codes(tmp_path, CLEAN_CB, ["R", "S", "X"])


def test_uncovered_value_stays_quiet_when_every_value_is_claimed(tmp_path):
    assert "UNCOVERED_VALUE" not in codes(tmp_path, CLEAN_CB, ["R", "S"])


# --- POPULATED_FILLER ------------------------------------------------------

FILLER_CB = """
01  REC.
    05  CODE-X   PIC X(01).
    05  FILLER   PIC X(04).
"""


def test_populated_filler_fires_when_filler_carries_data(tmp_path):
    assert "POPULATED_FILLER" in codes(tmp_path, FILLER_CB, ["SBTCH", "RBTCH"])


def test_populated_filler_stays_quiet_when_filler_is_blank(tmp_path):
    assert "POPULATED_FILLER" not in codes(tmp_path, FILLER_CB, ["S    ", "R    "])


# --- INVALID_PACKED --------------------------------------------------------

PACKED_CB = """
01  REC.
    05  TOT   PIC S9(05)V99 COMP-3.
"""


def test_invalid_packed_fires_when_the_field_is_not_really_packed(tmp_path):
    layout = parse(PACKED_CB)
    bad = b"ABCDE"                      # EBCDIC letters: nibbles above 9
    path = tmp_path / "p.dat"
    path.write_bytes(bad * 4)
    found = {f.code for f in evaluate(layout, scan(str(path), layout))}
    assert "INVALID_PACKED" in found


def test_invalid_packed_stays_quiet_on_real_packed_decimal(tmp_path):
    layout = parse(PACKED_CB)
    good = encode_packed(Decimal("-1234.56"), digits=7, scale=2)
    assert len(good) == 4
    path = tmp_path / "p.dat"
    path.write_bytes(good * 4)
    found = {f.code for f in evaluate(layout, scan(str(path), layout))}
    assert "INVALID_PACKED" not in found


# --- the layout arithmetic itself ------------------------------------------

def test_record_length_is_the_sum_of_the_field_widths():
    layout = parse("""
    01  REC.
        05  A   PIC X(03).
        05  B   PIC S9(07)V99 COMP-3.
        05  C   PIC S9(04) COMP.
        05  D   PIC 9(05).
    """)
    assert layout.record_length() == 3 + 5 + 2 + 5


def test_redefines_shares_bytes_instead_of_adding_them():
    layout = parse("""
    01  REC.
        05  A          PIC X(10).
        05  A-PARTS  REDEFINES A.
            10  A-1    PIC X(04).
            10  A-2    PIC X(06).
        05  B          PIC X(02).
    """)
    assert layout.record_length() == 12
    assert layout.find("A-1").offset == 0
    assert layout.find("B").offset == 10
