"""Properties that must hold for ANY input, not just the ones we thought of.

The example-based tests elsewhere check the cases someone imagined. These check
invariants: things that must be true of every value, every code page, every
copybook in the repository. They are where the unimagined cases get caught.
"""

from __future__ import annotations

import os
import random
import resource
from decimal import Decimal
from pathlib import Path

import pytest

from overpunch.copybook import parse, parse_file
from overpunch.decode import (decode_binary, decode_display, decode_hex_float,
                              decode_packed, encode_hex_float,
                              encode_overpunch_bytes, encode_packed)
from overpunch.findings import evaluate
from overpunch.layout import LayoutError
from overpunch.probe import LayoutMismatch, iter_records, scan

ROOT = Path(__file__).resolve().parents[1]
PAGES = ("cp037", "cp273", "cp500", "cp1026")


def every_copybook():
    for d in ("demo", "samples/data", "demo/carddemo"):
        for p in sorted((ROOT / d).glob("*.cpy")):
            yield p


# --- round trips ------------------------------------------------------------

@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("scale", [0, 2])
def test_display_round_trips_for_every_value_and_page(page, scale):
    """encode then decode must return exactly what went in - including -0,
    which is the value that broke on German code pages."""
    pic = parse(f"000100 01  R.\n000200     05  A  PIC S9(06)V{'99' if scale else ''}.\n"
                if scale else
                "000100 01  R.\n000200     05  A  PIC S9(08).\n").find("A").pic
    digits = pic.digits
    rng = random.Random(99)
    values = [Decimal(0), Decimal("-0.00" if scale else "0")]
    values += [Decimal(str(round(rng.uniform(-10**4, 10**4), scale))) for _ in range(200)]
    values += [Decimal(-(10**(digits - scale) - 1)).scaleb(-scale) if scale
               else Decimal(-(10**digits - 1))]
    for v in values:
        scaled = int(v.scaleb(scale))
        raw = encode_overpunch_bytes(str(abs(scaled)).rjust(digits, "0"),
                                     scaled < 0, page)
        assert len(raw) == digits
        back = decode_display(raw, pic, encoding=page)
        assert back == v.quantize(Decimal(1).scaleb(-scale)), (page, v, raw.hex())


@pytest.mark.parametrize("digits,scale", [(3, 0), (5, 2), (7, 2), (9, 2),
                                          (11, 2), (16, 4), (18, 0)])
def test_packed_round_trips_at_every_width(digits, scale):
    """COMP-3 is (digits // 2) + 1 bytes, and odd digit counts are the awkward
    case because the leading nibble is a pad."""
    rng = random.Random(digits * 31 + scale)
    for _ in range(150):
        magnitude = 10 ** min(digits - scale - 1, 9)
        v = Decimal(str(round(rng.uniform(-magnitude, magnitude), scale)))
        raw = encode_packed(v, digits, scale)
        assert len(raw) == digits // 2 + 1
        assert decode_packed(raw, scale) == v


@pytest.mark.parametrize("width", [4, 8])
def test_hex_float_round_trips(width):
    rng = random.Random(width)
    for _ in range(200):
        v = Decimal(str(round(rng.uniform(-1e5, 1e5), 4)))
        back = decode_hex_float(encode_hex_float(v, width))
        assert abs(back - v) < abs(v) * Decimal("1e-5") + Decimal("1e-6")


@pytest.mark.parametrize("width", [2, 4, 8])
def test_binary_round_trips_including_the_negative_extreme(width):
    lo, hi = -(2 ** (8 * width - 1)), 2 ** (8 * width - 1) - 1
    rng = random.Random(width)
    for v in [lo, hi, 0, -1, 1] + [rng.randint(lo, hi) for _ in range(100)]:
        assert decode_binary(v.to_bytes(width, "big", signed=True), True) == v


# --- metamorphic ------------------------------------------------------------

def build_file(tmp_path, records=100, name="m.dat"):
    cpy = "000100 01  R.\n000200     05  ID  PIC 9(04).\n000300     05  AMT PIC S9(06)V99.\n"
    layout = parse(cpy)
    rng = random.Random(7)
    rows = []
    for i in range(records):
        v = Decimal(str(round(rng.uniform(-500, 900), 2)))
        s = int(v.scaleb(2))
        rows.append(f"{i % 10000:04d}".encode("cp037") +
                    encode_overpunch_bytes(str(abs(s)).rjust(8, "0"), s < 0, "cp037"))
    path = tmp_path / name
    path.write_bytes(b"".join(rows))
    return layout, path, rows


def test_doubling_the_file_doubles_every_total(tmp_path):
    """A pure aggregation must scale exactly. Anything that does not is state
    leaking between records."""
    layout, path, rows = build_file(tmp_path)
    one = scan(str(path), layout)["AMT"]
    (tmp_path / "double.dat").write_bytes(b"".join(rows) * 2)
    two = scan(str(tmp_path / "double.dat"), layout)["AMT"]
    assert two.examined == one.examined * 2
    assert two.overpunch_negative == one.overpunch_negative * 2
    assert two.sum_correct == one.sum_correct * 2


def test_reordering_records_changes_no_aggregate(tmp_path):
    """Order must not matter to a count or a sum."""
    layout, path, rows = build_file(tmp_path)
    before = scan(str(path), layout)["AMT"]
    shuffled = rows[:]
    random.Random(3).shuffle(shuffled)
    (tmp_path / "shuf.dat").write_bytes(b"".join(shuffled))
    after = scan(str(tmp_path / "shuf.dat"), layout)["AMT"]
    assert (after.examined, after.overpunch_negative, after.sum_correct) == \
           (before.examined, before.overpunch_negative, before.sum_correct)


def test_appending_a_trailing_filler_moves_nothing_before_it(tmp_path):
    """Adding a field at the end must not disturb any field ahead of it."""
    base = "000100 01  R.\n000200     05  ID  PIC 9(04).\n000300     05  AMT PIC S9(06)V99.\n"
    extended = base + "000400     05  PAD PIC X(05).\n"
    a, b = parse(base), parse(extended)
    for name in ("ID", "AMT"):
        assert a.find(name).offset == b.find(name).offset
        assert a.find(name).total_size() == b.find(name).total_size()
    assert b.record_length() == a.record_length() + 5


# --- boundaries -------------------------------------------------------------

def test_an_empty_file_yields_no_records_and_does_not_crash(tmp_path):
    path = tmp_path / "empty.dat"
    path.write_bytes(b"")
    layout = parse("000100 01  R.\n000200     05  A PIC X(10).\n")
    assert list(iter_records(str(path), layout.record_length())) == []
    assert scan(str(path), layout)["A"].examined == 0
    assert evaluate(layout, scan(str(path), layout)) == []


def test_a_file_one_byte_short_is_refused(tmp_path):
    layout = parse("000100 01  R.\n000200     05  A PIC X(10).\n")
    path = tmp_path / "short.dat"
    path.write_bytes(b"\x40" * 29)
    with pytest.raises(LayoutMismatch) as exc:
        list(iter_records(str(path), layout.record_length()))
    assert exc.value.remainder == 9


def test_a_single_record_file_works(tmp_path):
    layout = parse("000100 01  R.\n000200     05  A PIC X(10).\n")
    path = tmp_path / "one.dat"
    path.write_bytes("HELLO     ".encode("cp037"))
    assert scan(str(path), layout)["A"].examined == 1


@pytest.mark.parametrize("fill", [b"\x00", b"\xFF", b"\x40"])
def test_pathological_bytes_do_not_crash_the_scan(tmp_path, fill):
    """Low values, high values and spaces all appear in real extracts."""
    layout = parse("000100 01  R.\n"
                   "000200     05  T PIC X(04).\n"
                   "000300     05  N PIC S9(04).\n"
                   "000400     05  P PIC S9(03) COMP-3.\n")
    path = tmp_path / f"fill{fill.hex()}.dat"
    path.write_bytes(fill * (layout.record_length() * 20))
    findings = evaluate(layout, scan(str(path), layout))
    assert isinstance(findings, list)          # it must survive, not be right


# --- structural invariants over every copybook here -------------------------

@pytest.mark.parametrize("path", list(every_copybook()),
                         ids=lambda p: p.name)
def test_no_two_fields_overlap_unless_one_redefines(path):
    layout = parse_file(str(path))
    redefined = {f.name.upper() for f in layout.walk_all() if f.redefines}
    for f in layout.walk_all():
        if f.redefines:
            redefined.update(c.name.upper() for c in layout.walk_all()
                             if c.parent is f)

    spans = []
    for f in layout.elementary_fields():
        node, shadowed = f, False
        while node is not None:
            if node.redefines:
                shadowed = True
            node = node.parent
        if not shadowed:
            spans.append((f.offset, f.offset + f.total_size(), f.name))
    spans.sort()
    for (s1, e1, n1), (s2, e2, n2) in zip(spans, spans[1:]):
        assert e1 <= s2, f"{n1} [{s1},{e1}) overlaps {n2} [{s2},{e2}) in {path.name}"


@pytest.mark.parametrize("path", list(every_copybook()), ids=lambda p: p.name)
def test_no_field_reaches_past_the_end_of_the_record(path):
    layout = parse_file(str(path))
    for f in layout.elementary_fields():
        assert f.offset + f.total_size() <= layout.record_length(), f.name


@pytest.mark.parametrize("path", list(every_copybook()), ids=lambda p: p.name)
def test_every_finding_names_a_field_that_exists(path):
    """A rule that reports a field the layout does not contain is a rule with a
    bug in its bookkeeping."""
    layout = parse_file(str(path))
    names = {f.name for f in layout.elementary_fields()} | {"FILLER"}
    dat = path.with_suffix(".dat")
    if not dat.exists():
        pytest.skip("no data file")
    try:
        stats = scan(str(dat), layout)
    except LayoutMismatch:
        # samples/wrong-copybook and samples/variable-blocked exist precisely to
        # not divide; the refusal is their expected behaviour
        pytest.skip("this pairing is meant to be refused")
    for f in evaluate(layout, stats):
        head = f.field.split(" @ ")[0]
        assert head in names, f"{f.code} names {f.field!r}"


# --- determinism ------------------------------------------------------------

def test_scanning_the_same_file_twice_gives_the_same_answer(tmp_path):
    layout, path, _ = build_file(tmp_path)
    a = evaluate(layout, scan(str(path), layout))
    b = evaluate(layout, scan(str(path), layout))
    assert [(f.code, f.field, f.evidence, f.impact) for f in a] == \
           [(f.code, f.field, f.evidence, f.impact) for f in b]


# --- scale ------------------------------------------------------------------

def test_a_large_file_is_streamed_not_loaded(tmp_path):
    """The files this tool exists for are gigabytes.

    iter_records used to read the whole file before yielding anything: 20 MB
    resident for a 21 MB file, growing linearly. Nothing caught it because
    every fixture is tiny.
    """
    layout = parse("000100 01  R.\n000200     05  A PIC X(50).\n")
    path = tmp_path / "big.dat"
    block = b"\x40" * (layout.record_length() * 1000)
    with open(path, "wb") as fh:
        for _ in range(400):                    # 20 MB
            fh.write(block)
    size = os.path.getsize(path)

    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    count = sum(1 for _ in iter_records(str(path), layout.record_length()))
    grew_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before

    assert count == size // layout.record_length()
    assert grew_kb < size / 1024 / 4, (
        f"memory grew {grew_kb/1024:.1f} MB reading a {size/1e6:.0f} MB file - "
        f"that is not streaming")
