"""Every sample in the corpus, held to the answer its manifest declares.

The expected findings are written down in samples/MANIFEST.json BEFORE the tool
is run, by the person who planted them. A test that asks the code what the
answer is and then agrees with it proves the code runs, not that it is right.

    python samples/build_samples.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from overpunch.copybook import parse_file
from overpunch.findings import evaluate
from overpunch.probe import LayoutMismatch, scan

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "samples" / "data"
MANIFEST = ROOT / "samples" / "MANIFEST.json"

pytestmark = pytest.mark.skipif(
    not MANIFEST.exists() or not DATA.exists(),
    reason="run python samples/build_samples.py to generate the corpus")

CASES = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
IDS = [c["name"] for c in CASES]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_layout_is_the_width_the_manifest_declares(case):
    layout = parse_file(str(DATA / case["copybook"]))
    assert layout.record_length() == case["record_bytes"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_each_sample_produces_exactly_the_findings_it_was_built_for(case):
    if not case["data"]:
        pytest.skip("layout-only sample")
    layout = parse_file(str(DATA / case["copybook"]))
    path = str(DATA / case["data"])
    expected = set(case["expect"])

    if "LAYOUT_MISMATCH" in expected:
        # not a finding: the scan refuses to run at all, because a copybook that
        # does not divide the file cannot describe it
        with pytest.raises(LayoutMismatch):
            scan(path, layout, encoding=case["encoding"])
        return

    findings = evaluate(layout, scan(path, layout, encoding=case["encoding"]))
    got = {f.code for f in findings}
    assert got == expected, (
        f"{case['name']}: expected {sorted(expected)}, got {sorted(got)}")


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_record_count_is_what_was_generated(case):
    if not case["data"] or case["records"] is None:
        pytest.skip("no declared record count")
    layout = parse_file(str(DATA / case["copybook"]))
    size = (DATA / case["data"]).stat().st_size
    assert size % layout.record_length() == 0
    assert size // layout.record_length() == case["records"]


def test_the_clean_sample_really_is_clean():
    """Stated separately because it is the claim most easily lost.

    Every other sample checks that something IS found. This one checks that
    nothing is - which is what stops the rules drifting into noise.
    """
    case = next(c for c in CASES if c["name"] == "clean")
    layout = parse_file(str(DATA / case["copybook"]))
    findings = evaluate(layout, scan(str(DATA / case["data"]), layout))
    assert findings == []


def test_the_corpus_covers_the_rules_that_matter():
    """A corpus is only as good as its coverage. This names the gap out loud."""
    covered = {code for c in CASES for code in c["expect"]}
    for code in ("TRAILING_SIGN", "IMPLIED_DECIMAL", "INVALID_PACKED",
                 "NEVER_POPULATED", "UNCOVERED_VALUE", "LAYOUT_MISMATCH"):
        assert code in covered, f"no sample exercises {code}"
