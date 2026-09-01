"""Against a real mainframe application, not one we wrote.

AWS publishes CardDemo: a COBOL credit-card system with genuine EBCDIC data and
the copybooks describing it, authored by people with no knowledge of this tool.
Everything else here is synthetic by design; this is the control.

Skipped unless the files have been fetched:

    python scripts/fetch_carddemo.py
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from overpunch.copybook import parse_file
from overpunch.decode import decode_field, decode_text
from overpunch.findings import evaluate
from overpunch.probe import iter_records, scan

DATA = Path(__file__).resolve().parents[1] / "demo" / "carddemo"
pytestmark = pytest.mark.skipif(
    not (DATA / "DALYTRAN.PS").exists(),
    reason="run scripts/fetch_carddemo.py to fetch the real-world fixtures")


def test_account_layout_matches_the_length_its_own_comment_declares():
    """CVACT01Y.cpy says 'RECLN 300' in a comment we do not parse.

    The parser reaches 300 from the field widths alone, and the data file is an
    exact multiple of it. Two independent sources, neither of them us.
    """
    layout = parse_file(str(DATA / "CVACT01Y.cpy"))
    assert layout.record_length() == 300
    assert (DATA / "ACCTDATA.PS").stat().st_size % 300 == 0


def test_transaction_layout_divides_its_file_exactly():
    layout = parse_file(str(DATA / "CVTRA06Y.cpy"))
    assert layout.record_length() == 350
    assert (DATA / "DALYTRAN.PS").stat().st_size == 350 * 300


def test_the_real_file_carries_real_negative_amounts():
    """50 of 300 transactions are credits, and reading them unsigned
    overstates the total by roughly 47%."""
    layout = parse_file(str(DATA / "CVTRA06Y.cpy"))
    stats = scan(str(DATA / "DALYTRAN.PS"), layout)
    amt = stats["DALYTRAN-AMT"]
    assert amt.overpunch_negative == 50
    assert amt.examined == 300
    assert amt.sum_correct == Decimal("104801.54")
    assert amt.sum_abs == Decimal("153600.12")


def test_the_negatives_are_corroborated_by_a_different_field():
    """Every negative amount describes a return.

    The sign is decoded from the last byte of one field; the word 'Return'
    comes from a text field 100 bytes away. Agreement between them is evidence
    the decode is right, not just self-consistent.
    """
    layout = parse_file(str(DATA / "CVTRA06Y.cpy"))
    amt, desc = layout.find("DALYTRAN-AMT"), layout.find("DALYTRAN-DESC")
    negatives = returns = 0
    for rec in iter_records(str(DATA / "DALYTRAN.PS"), layout.record_length()):
        value = decode_field(rec[amt.offset:amt.offset + amt.total_size()], amt)
        if value < 0:
            negatives += 1
            text = decode_text(rec[desc.offset:desc.offset + desc.total_size()])
            if "return" in text.lower():
                returns += 1
    assert negatives == 50
    assert returns == negatives


def test_the_scan_reports_the_sign_as_critical_on_real_data():
    layout = parse_file(str(DATA / "CVTRA06Y.cpy"))
    findings = evaluate(layout, scan(str(DATA / "DALYTRAN.PS"), layout))
    sign = next(f for f in findings if f.code == "TRAILING_SIGN")
    assert sign.severity == "critical"
    assert sign.field == "DALYTRAN-AMT"
    assert "104,801.54" in sign.impact and "153,600.12" in sign.impact


def test_a_clean_file_produces_no_critical_finding():
    """The account file has no negative balances. The tool must say so quietly.

    A detector that finds something alarming in every file is not a detector.
    """
    layout = parse_file(str(DATA / "CVACT01Y.cpy"))
    findings = evaluate(layout, scan(str(DATA / "ACCTDATA.PS"), layout))
    assert not [f for f in findings if f.severity == "critical"]
    assert any(f.code == "SIGN_PRESENT_ALL_POSITIVE" for f in findings)
