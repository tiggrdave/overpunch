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


def test_a_true_but_inconsequential_hypothesis_is_inert_not_refuted(tmp_path):
    """The sign IS in the last byte, and no record is negative.

    Calling this REFUTED would tell the reader the model misread the bytes. It
    did not. The distinction between "wrong" and "right but harmless here" is
    the whole reason a third verdict exists.
    """
    v = verdict_for(tmp_path, Hypothesis("AMT", "TRAILING_SIGN", "looks signed"),
                    negatives=0, positives=4)
    assert v.verdict == "INERT"
    assert "every record is positive" in v.evidence["note"]


def test_a_hypothesis_about_a_field_with_no_sign_at_all_is_refuted(tmp_path):
    """CODE-X is PIC X. There is no sign anywhere in it."""
    v = verdict_for(tmp_path, Hypothesis("CODE-X", "TRAILING_SIGN", "guessing"))
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


# --- reply parsing against the shapes a real reasoning model produces --------
# Every case below is one that actually broke against nvidia/nemotron-3-super.

def test_reasoning_prose_with_braces_before_the_answer_is_survived():
    """Taking the first '{' picks up a brace out of the model's thinking."""
    reply = (
        "We need to examine the profile. Consider {BIL-AMT} and note that "
        "distinct_values=0 seems odd. Let me set out the answer.\n\n"
        '{"hypotheses": [{"field": "BIL-AMT", "kind": "TRAILING_SIGN", '
        '"rationale": "r", "meaning": "m"}]}')
    hyps = parse_hypotheses(reply)
    assert len(hyps) == 1 and hyps[0].field == "BIL-AMT"


def test_trailing_prose_after_the_answer_is_survived():
    reply = ('{"hypotheses": [{"field": "A", "kind": "TRAILING_SIGN"}]}\n'
             "That covers the significant risks {see above}.")
    assert len(parse_hypotheses(reply)) == 1


def test_a_truncated_reply_is_an_error_not_a_silent_empty_result():
    """A reply cut off by max_tokens must never look like 'no problems found'."""
    import pytest as _pytest
    from overpunch.explain import NemotronError
    reply = ('Reasoning about the layout.\n{"hypotheses": [{"field":"A",'
             '"kind":"TRAILING_SIGN","rationale":"r"},{"field')
    with _pytest.raises(NemotronError) as exc:
        parse_hypotheses(reply)
    assert "max-tokens" in str(exc.value)


def test_a_fenced_block_amid_reasoning_is_found():
    reply = ("Thinking: the object {a} is irrelevant.\n```json\n"
             '{"hypotheses": [{"field": "B", "kind": "IMPLIED_DECIMAL"}]}\n```')
    assert parse_hypotheses(reply)[0].field == "B"


def test_profile_reports_no_distinct_count_for_numeric_fields(tmp_path):
    """Reporting 0 for a figure that was never measured is a false statement."""
    layout, stats, _ = build(tmp_path, 2, 2)
    prof = profile(layout, stats)
    by_name = {f["name"]: f for f in prof["fields"]}
    assert "distinct_values" not in by_name["AMT"]["observed"]
    assert "distinct_values" in by_name["CODE-X"]["observed"]


# --- the two paths must not drift ------------------------------------------

DRIFT_CB = """
01  REC.
    05  MONEY-AMT   PIC S9(06)V99.
    05  ID-NO       PIC 9(07).
    05  CODE-Y      PIC X(01).
        88  IS-R    VALUE 'R'.
    05  FILL-AREA   PIC X(04).
"""


def _drift_file(tmp_path):
    """A fixture built to make the two paths disagree if they can.

    MONEY-AMT is scaled AND small, so the width-underfill condition is TRUE of
    the raw numbers and FALSE once the money guard is applied. A fixture whose
    values leave both versions agreeing cannot tell the paths apart, and an
    earlier version of this test used exactly such a fixture and passed while
    the drift was present.
    """
    from overpunch.probe import scan as _scan
    layout = parse(DRIFT_CB)
    recs = [encode_overpunch("00001250", False) + "0012345" + "R" + "BTCH",
            encode_overpunch("00003400", True) + "0067890" + "R" + "BTCH"]
    path = tmp_path / "drift.dat"
    path.write_bytes("".join(recs).encode("cp037"))
    return layout, _scan(str(path), layout), str(path)


def test_the_drift_fixture_can_actually_tell_the_paths_apart(tmp_path):
    """Guard on the guard: prove the fixture exercises the divergent branch.

    If MONEY-AMT stops being a field where the money guard changes the answer,
    the consistency test below silently stops testing anything.
    """
    from overpunch import predicates as pred
    layout, stats, _ = _drift_file(tmp_path)
    fld = layout.find("MONEY-AMT")
    st = stats["MONEY-AMT"]
    unguarded = bool(st.max_significant_digits and
                     st.max_significant_digits <= fld.pic.int_digits - 1)
    assert unguarded is True, "fixture no longer triggers the unguarded condition"
    assert pred.width_underfill(fld, st) is False, "money guard no longer applies"


def test_scan_and_explain_agree_on_every_shared_rule(tmp_path):
    """`scan` and `explain` ask the same questions of the same measurements.

    They used to answer differently: `scan` had learned to stop nagging about
    headroom on money fields and `explain` had not, so one command reported no
    finding where the other reported CONFIRMED. Nothing caught it, because each
    path was only ever tested against itself.
    """
    from overpunch.findings import evaluate
    from overpunch.explain import TESTABLE_KINDS

    for make in (lambda: build(tmp_path, negatives=3, positives=1),
                 lambda: _drift_file(tmp_path)):
        layout, stats, path = make()
        scan_codes = {(f.field, f.code) for f in evaluate(layout, stats)}

        for fld in layout.elementary_fields():
            for kind in TESTABLE_KINDS:
                if kind in {"WRONG_ENCODING", "DATE_FIELD"}:
                    continue                  # no counterpart rule in scan
                v = adjudicate([Hypothesis(fld.name, kind, "")],
                               layout, stats, path)[0]
                in_scan = (fld.name, kind) in scan_codes
                if v.verdict == "CONFIRMED":
                    assert in_scan, (
                        f"{fld.name}/{kind}: explain says CONFIRMED but scan "
                        f"reports no such finding")
                elif v.verdict == "REFUTED":
                    assert not in_scan, (
                        f"{fld.name}/{kind}: explain says REFUTED but scan "
                        f"reports it")
