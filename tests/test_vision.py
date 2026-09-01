"""Reading a copybook off a picture, and refusing to believe it.

Both fixtures here are real replies from nvidia/nemotron-parse, reading the same
image of the same printout. One is correct. The other is not, and the incorrect
one PARSES AS VALID COBOL with a plausible field list - which is the entire
reason the checks exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from overpunch import vision
from overpunch.copybook import parse, parse_file
from overpunch.findings import evaluate
from overpunch.probe import scan
from overpunch.vision import (Reconstruction, VisionError, declared_length_in,
                              reconstruct, to_copybook)

FIX = Path(__file__).resolve().parent / "fixtures"
DEMO = Path(__file__).resolve().parents[1] / "demo"
REAL_CPY = DEMO / "carddemo" / "CVTRA06Y.cpy"
REAL_DAT = DEMO / "carddemo" / "DALYTRAN.PS"
TRUE_LENGTH = 350
DATA_BYTES = 105_000


def blocks(name):
    return json.loads((FIX / name).read_text())


# --- reassembling the page --------------------------------------------------

def test_a_monospace_listing_comes_back_as_a_latex_table():
    """Aligned columns look like a table to a document parser, so the fields
    arrive inside \\begin{tabular} rather than as lines."""
    text, kept = to_copybook(blocks("parse_good.json"))
    assert "\\begin{tabular}" in " ".join(kept)
    assert "05 DALYTRAN-ID PIC X(16)." in text


def test_the_01_level_is_ordered_first_whatever_order_the_page_was_read_in():
    text, _ = to_copybook(blocks("parse_good.json"))
    assert text.splitlines()[0].startswith("01 ")


def test_a_good_read_reconstructs_the_real_copybook_exactly():
    text, _ = to_copybook(blocks("parse_good.json"))
    recovered, original = parse(text), parse_file(str(REAL_CPY))
    assert recovered.record_length() == original.record_length() == TRUE_LENGTH

    def shape(l):
        return {f.name: (f.offset, f.total_size(), f.pic.raw)
                for f in l.elementary_fields()}
    assert shape(recovered) == shape(original)


@pytest.mark.skipif(not REAL_DAT.exists(),
                    reason="run scripts/fetch_carddemo.py")
def test_the_recovered_copybook_finds_the_same_defects_in_the_real_data():
    """The end of the chain: a picture of a printout, and the same finding.

    Not merely the same field names - the same money, to the cent.
    """
    text, _ = to_copybook(blocks("parse_good.json"))

    def results(layout):
        return [(f.code, f.field, f.impact)
                for f in evaluate(layout, scan(str(REAL_DAT), layout))]

    assert results(parse(text)) == results(parse_file(str(REAL_CPY)))
    sign = next(f for f in results(parse(text)) if f[0] == "TRAILING_SIGN")
    assert "104,801.54" in sign[2] and "153,600.12" in sign[2]


# --- the misread ------------------------------------------------------------

def test_the_misread_is_valid_cobol_and_still_wrong():
    """This is why arithmetic and not confidence decides.

    The bad read produces a parseable copybook with a sensible-looking field
    list. Nothing about its SHAPE gives it away.
    """
    text, _ = to_copybook(blocks("parse_misread.json"))
    layout = parse(text)                       # parses without complaint
    assert layout.elementary_fields()          # has fields
    assert layout.record_length() != TRUE_LENGTH


def test_the_misread_fails_the_checks(monkeypatch):
    monkeypatch.setattr(vision, "read_image",
                        lambda *a, **k: blocks("parse_misread.json"))
    r = reconstruct(b"", "key", data_bytes=DATA_BYTES)
    assert not r.proved
    failed = [name for name, ok, _ in r.checks if not ok]
    assert "divides the real data file exactly" in failed
    assert "matches the length printed on the page" in failed


def test_a_good_read_passes_every_check(monkeypatch):
    monkeypatch.setattr(vision, "read_image",
                        lambda *a, **k: blocks("parse_good.json"))
    r = reconstruct(b"", "key", data_bytes=DATA_BYTES)
    assert r.proved
    assert [ok for _, ok, _ in r.checks] == [True, True, True]


def test_the_length_printed_on_the_page_is_read_off_the_page():
    """Nothing parses comments. The model read 'RECLN = 350' off the paper, and
    the parser reached 350 from the field widths. Two routes, one number."""
    _, kept = to_copybook(blocks("parse_good.json"))
    assert declared_length_in(" ".join(kept)) == TRUE_LENGTH


# --- retry until proved -----------------------------------------------------

def test_it_retries_until_a_reading_is_proved(monkeypatch):
    """An unreliable reader plus a decisive check is a reliable pipeline."""
    seq = [blocks("parse_misread.json"), blocks("parse_misread.json"),
           blocks("parse_good.json")]
    calls = {"n": 0}

    def fake(*a, **k):
        out = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return out

    monkeypatch.setattr(vision, "read_image", fake)
    r = reconstruct(b"", "key", data_bytes=DATA_BYTES, attempts=5)
    assert r.proved
    assert r.attempts == 3
    assert calls["n"] == 3


def test_it_gives_up_honestly_rather_than_returning_a_bad_read(monkeypatch):
    monkeypatch.setattr(vision, "read_image",
                        lambda *a, **k: blocks("parse_misread.json"))
    r = reconstruct(b"", "key", data_bytes=DATA_BYTES, attempts=3)
    assert not r.proved
    assert r.attempts == 3


def test_an_empty_reply_is_an_error_not_an_empty_copybook():
    """A 200 with no content is the failure that looks like success."""
    import urllib.request

    class FakeResponse:
        def read(self): return b""
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        class R(FakeResponse):
            def read(self):
                return json.dumps({"choices": [{"message": {"content": None,
                                   "tool_calls": []}, "finish_reason": "stop"}]}).encode()
        return R()

    import overpunch.vision as v
    original = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        with pytest.raises(VisionError) as exc:
            v.read_image(b"", "key")
        assert "no text blocks" in str(exc.value)
    finally:
        urllib.request.urlopen = original
