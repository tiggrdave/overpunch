"""The model does not get a vote.

These tests exist because the whole claim of this project is that a wrong
proposal cannot become a finding. That claim is worth exactly as much as the
test that plants a wrong proposal and requires it to come back REFUTED.
"""

from __future__ import annotations

from overpunch.copybook import parse
from overpunch.decode import encode_overpunch
from overpunch.explain import Hypothesis, adjudicate, parse_hypotheses, profile
from overpunch.probe import scan

CB = """
01  REC.
    05  AMT      PIC S9(06)V99.
    05  CODE-X   PIC X(01).
        88  IS-A   VALUE 'A'.
"""


def build(tmp_path, negatives: int, positives: int):
    layout = parse(CB)
    recs = ([encode_overpunch("00120050", True) + "A"] * negatives +
            [encode_overpunch("00120050", False) + "A"] * positives)
    path = tmp_path / "d.dat"
    path.write_bytes("".join(recs).encode("cp037"))
    stats = scan(str(path), layout)
    return layout, stats, str(path)


def verdict_for(tmp_path, hyp, negatives=3, positives=1):
    layout, stats, path = build(tmp_path, negatives, positives)
    return adjudicate([hyp], layout, stats, path)[0]


def test_a_true_hypothesis_is_confirmed(tmp_path):
    v = verdict_for(tmp_path, Hypothesis("AMT", "TRAILING_SIGN", "looks signed"))
    assert v.verdict == "CONFIRMED"
    assert v.evidence["negative_records"] == "3"


def test_the_same_hypothesis_is_refuted_when_the_file_disagrees(tmp_path):
    """Identical proposal, file with no negatives in it. The bytes decide."""
    v = verdict_for(tmp_path, Hypothesis("AMT", "TRAILING_SIGN", "looks signed"),
                    negatives=0, positives=4)
    assert v.verdict == "REFUTED"


def test_a_hallucinated_field_name_is_refuted_not_reported(tmp_path):
    v = verdict_for(tmp_path, Hypothesis("CHG-TOTAL-BALANCE", "TRAILING_SIGN",
                                         "confidently invented"))
    assert v.verdict == "REFUTED"
    assert "no such field" in v.evidence["reason"]


def test_a_hypothesis_outside_the_vocabulary_is_untestable(tmp_path):
    v = verdict_for(tmp_path, Hypothesis("AMT", "FIELD_IS_PROBABLY_A_SSN",
                                         "plausible but unprovable"))
    assert v.verdict == "UNTESTABLE"


def test_uncovered_value_is_refuted_when_every_value_is_claimed(tmp_path):
    v = verdict_for(tmp_path, Hypothesis("CODE-X", "UNCOVERED_VALUE", ""))
    assert v.verdict == "REFUTED"


def test_reply_parsing_survives_fences_and_prose():
    reply = ('Sure! Here is the analysis.\n```json\n'
             '{"hypotheses": [{"field": "amt", "kind": "trailing_sign", '
             '"rationale": "r", "meaning": "m"}]}\n```\nHope that helps.')
    hyps = parse_hypotheses(reply)
    assert len(hyps) == 1
    assert hyps[0].field == "AMT" and hyps[0].kind == "TRAILING_SIGN"


def test_profile_carries_no_numeric_field_values(tmp_path):
    """A profile of a real file must be safe to send. Counts and shapes only."""
    layout, stats, _ = build(tmp_path, 3, 1)
    prof = profile(layout, stats)
    blob = repr(prof)
    assert "120050" not in blob
    assert prof["fields"][0]["observed"]["records"] == 4


GROUP_CB = """
01  REC.
    05  EFF-DATE.
        10  D-CC  PIC 9(02).
        10  D-YY  PIC 9(02).
        10  D-MM  PIC 9(02).
        10  D-DD  PIC 9(02).
"""


def _dates(tmp_path, values):
    from overpunch.probe import scan as _scan
    layout = parse(GROUP_CB)
    path = tmp_path / "dates.dat"
    path.write_bytes("".join(values).encode("cp037"))
    return layout, _scan(str(path), layout), str(path)


def test_date_hypothesis_is_testable_on_a_group_item(tmp_path):
    layout, stats, path = _dates(tmp_path, ["20260120", "19991231", "20200229"])
    v = adjudicate([Hypothesis("EFF-DATE", "DATE_FIELD", "")], layout, stats, path)[0]
    assert v.verdict == "CONFIRMED"
    assert v.evidence["share"] == "100.0%"


def test_date_hypothesis_is_refuted_when_the_bytes_are_not_dates(tmp_path):
    layout, stats, path = _dates(tmp_path, ["20261320", "20260230", "99999999"])
    v = adjudicate([Hypothesis("EFF-DATE", "DATE_FIELD", "")], layout, stats, path)[0]
    assert v.verdict == "REFUTED"
