"""Proposing the answers a copybook cannot give, and refusing to take them on trust.

The model is not needed for any of this. What is being tested is the part that
decides whether a proposal survives: a key must be unique across the file, and a
discriminator must make its branch fit the records it claims better than the
records it does not.

Both tests are two-sided on purpose. The first version of the branch test was
not, and it rejected the CORRECT answer - a branch of PIC X fields accepts
anything printable, including a policy number, so it scored 100% on every record
and tied with the branch that was genuinely right.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from overpunch.copybook import parse_file
from overpunch.explain import NemotronError
from overpunch.plan import build
from overpunch.resolve import Proposal, adjudicate, build_prompt, parse_proposals

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))
CPY = ROOT / "demo" / "TORTURE.cpy"


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    from make_torture_data import build as build_data      # noqa: E402
    data, truth = build_data(300, 20261020)
    path = tmp_path_factory.mktemp("torture") / "TORTURE.dat"
    path.write_bytes(data)
    layout = parse_file(str(CPY))
    return layout, build(layout), str(path), truth


def verdict(fixture, proposal):
    layout, plan, path, _ = fixture
    return adjudicate([proposal], plan, layout, path)[0]


def key(names):
    return Proposal("primary_key", "PRIMARY_KEY", {"primary_key": names})


def branch(mapping, discriminator="TR-DISCRIMINATOR"):
    return Proposal("redefines:TR-PAYLOAD", "REDEFINES_BRANCH",
                    {"discriminator": discriminator, "map": mapping})


# --- the key ----------------------------------------------------------------

def test_the_obvious_single_field_key_is_refuted_by_the_data(fixture):
    """An account number identifies an account, but not a record: the same
    account numbers occur in both regions."""
    v = verdict(fixture, key(["TR-ACCOUNT"]))
    assert v.verdict == "REFUTED"
    assert v.evidence["distinct"] == "150" and v.evidence["records"] == "300"
    assert v.evidence["duplicates"] == "150"


def test_the_composite_key_is_confirmed(fixture):
    v = verdict(fixture, key(["TR-REGION", "TR-ACCOUNT"]))
    assert v.verdict == "CONFIRMED"
    assert v.evidence["duplicates"] == "0"


def test_an_invented_field_is_refuted_rather_than_ignored(fixture):
    v = verdict(fixture, key(["TR-CUSTOMER-ID"]))
    assert v.verdict == "REFUTED"
    assert "no such field" in v.evidence["reason"]


def test_an_empty_key_is_refused(fixture):
    v = verdict(fixture, key([]))
    assert v.verdict == "REFUTED"


# --- the discriminator ------------------------------------------------------

def test_the_right_mapping_is_confirmed(fixture):
    v = verdict(fixture, branch({"P": "TR-PAYLOAD-PERSON",
                                 "L": "TR-PAYLOAD-POLICY"}))
    assert v.verdict == "CONFIRMED"
    assert "TR-PAYLOAD-POLICY" in v.evidence["verdict_because"]


def test_the_same_mapping_applied_backwards_is_refuted(fixture):
    """The case a one-sided test cannot catch."""
    v = verdict(fixture, branch({"P": "TR-PAYLOAD-POLICY",
                                 "L": "TR-PAYLOAD-PERSON"}))
    assert v.verdict == "REFUTED"
    assert "WORSE" in v.evidence["verdict_because"]


def test_a_field_that_decides_nothing_is_refuted(fixture):
    """TR-Q-FLAG is also a one-character Y/N field. It is not this one."""
    v = verdict(fixture, branch({"Y": "TR-PAYLOAD-PERSON",
                                 "N": "TR-PAYLOAD-POLICY"}, "TR-Q-FLAG"))
    assert v.verdict == "REFUTED"
    assert "does not decide anything" in v.evidence["verdict_because"]


def test_a_discriminator_that_is_not_a_field_is_refuted(fixture):
    v = verdict(fixture, branch({"P": "TR-PAYLOAD-PERSON"}, "TR-PAYLOAD-TYPE"))
    assert v.verdict == "REFUTED"
    assert "no such field" in v.evidence["reason"]


def test_a_branch_that_does_not_redefine_the_target_is_refuted(fixture):
    v = verdict(fixture, branch({"P": "TR-KEY", "L": "TR-PAYLOAD-POLICY"}))
    assert v.verdict == "REFUTED"
    assert "not a branch" in v.evidence["reason"]


def test_a_value_that_never_occurs_is_refuted(fixture):
    v = verdict(fixture, branch({"Z": "TR-PAYLOAD-PERSON",
                                 "L": "TR-PAYLOAD-POLICY"}))
    assert v.verdict == "REFUTED"
    assert "never occurs" in v.evidence["verdict_because"]


# --- the prompt and the reply -----------------------------------------------

def test_the_prompt_carries_every_open_question_and_its_answer_shape(fixture):
    _, plan, _, _ = fixture
    prompt = build_prompt(plan, CPY.read_text())
    assert "redefines:TR-PAYLOAD" in prompt and "primary_key" in prompt
    assert "discriminator" in prompt          # the answer shape
    assert "TR-DISCRIMINATOR" in prompt       # the copybook itself


def test_a_reply_wrapped_in_reasoning_is_parsed(fixture):
    _, plan, _, _ = fixture
    reply = ('Let me look at the 88-levels {they are informative}.\n'
             '{"resolutions": [{"id": "primary_key", '
             '"resolution": {"primary_key": ["TR-REGION", "TR-ACCOUNT"]}, '
             '"why": "TR-KEY groups them"}]}')
    proposals = parse_proposals(reply, plan)
    assert len(proposals) == 1
    assert proposals[0].kind == "PRIMARY_KEY"
    assert proposals[0].resolution["primary_key"] == ["TR-REGION", "TR-ACCOUNT"]


def test_a_truncated_reply_raises_rather_than_proposing_nothing(fixture):
    _, plan, _, _ = fixture
    with pytest.raises(NemotronError):
        parse_proposals('{"resolutions": [{"id": "primary_key", "resol', plan)


def test_an_unknown_decision_kind_is_untestable(fixture):
    layout, plan, path, _ = fixture
    p = Proposal("something-else", "MYSTERY", {"x": 1})
    v = adjudicate([p], plan, layout, path)[0]
    assert v.verdict == "UNTESTABLE"


def test_a_real_captured_reply_parses_and_survives_the_data(fixture):
    """The reply in tests/fixtures/ came back from nemotron-3-super.

    It is committed so the prompt, the parser and the adjudicator are all tested
    together without a network call - and so a change to the prompt that stops
    the model answering usefully shows up here rather than in production.
    """
    layout, plan, path, _ = fixture
    reply = (Path(__file__).parent / "fixtures" / "resolve_reply.txt").read_text()
    proposals = parse_proposals(reply, plan)
    assert {p.kind for p in proposals} == {"PRIMARY_KEY", "REDEFINES_BRANCH"}
    verdicts = adjudicate(proposals, plan, layout, path)
    assert all(v.verdict == "CONFIRMED" for v in verdicts), \
        [(v.proposal.kind, v.verdict, v.evidence) for v in verdicts]
