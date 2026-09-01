"""EBCDIC is a family, not an encoding, and the sign does not live in the text.

Every national EBCDIC page agrees that the zone nibble of the last byte carries
the sign: 0xC_ positive, 0xD_ negative. They emphatically do not agree on what
those bytes mean as characters - 0xD0 is '}' in cp037, 'ü' in cp273 (German) and
'ğ' in cp1026 (Turkish).

An earlier version read the sign from the decoded character. On German data the
-0 overpunch was not recognised at all: the digit was dropped and the record
turned positive, moving the total on a 200-record file by 122,228 with nothing
reported. These tests exist so that cannot come back.
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from overpunch.copybook import parse
from overpunch.decode import decode_display, encode_overpunch_bytes, zone_sign
from overpunch.findings import evaluate
from overpunch.probe import scan

GERMAN_COPYBOOK = """\
000100* Kundenstammsatz - Datensatzbeschreibung (RECLN 60)
000200* Geaendert 1998 durch Abt. Datenverarbeitung
000300 01  KUNDEN-SATZ.
000400     05  KUNDEN-NR             PIC 9(08).
000500     05  KUNDEN-NAME           PIC X(20).
000600     05  GEBURTSDATUM          PIC 9(08).
000700     05  KONTOSTAND            PIC S9(08)V99.
000800     05  WAEHRUNG              PIC X(03).
000900     05  BEARBEITER-KUERZEL    PIC X(03).
001000     05  STATUS-KZ             PIC X(01).
001100         88  KUNDE-AKTIV       VALUE 'A'.
001200         88  KUNDE-GESPERRT    VALUE 'G'.
001300     05  FILLER                PIC X(07).
"""

NAMES = ["MÜLLER, HANS", "SCHÄFER, ANNA", "WEISS, JÖRG", "GROSSMANN, UTE",
         "KÖHLER, BÄRBEL", "STRAUSS, HEINZ", "ÖZDEMIR, FATMA", "WEIß, KLAUS"]


def german_file(tmp_path, encoding="cp273", records=200):
    """Written the way a mainframe writes it: bytes, with a zone-nibble sign."""
    rng = random.Random(11)
    out, negatives, total = bytearray(), 0, Decimal(0)
    for i in range(records):
        rec = str(10_000_000 + i).encode(encoding)
        rec += NAMES[i % len(NAMES)].ljust(20)[:20].encode(encoding)
        rec += f"19{rng.randrange(50,99)}{rng.randrange(1,13):02d}" \
               f"{rng.randrange(1,29):02d}".encode(encoding)
        balance = Decimal(str(round(rng.uniform(-5000, 20000), 2)))
        scaled = int(balance.scaleb(2))
        negatives += scaled < 0
        total += balance
        rec += encode_overpunch_bytes(str(abs(scaled)).rjust(10, "0"),
                                      scaled < 0, encoding)
        rec += ("EUR" + rng.choice(["MÜL", "SCH", "WEI"]) +
                rng.choice(["A", "G"]) + " " * 7).encode(encoding)
        assert len(rec) == 60
        out += rec
    path = tmp_path / f"kunde-{encoding}.dat"
    path.write_bytes(bytes(out))
    return str(path), negatives, total


def test_the_layout_matches_the_length_its_comment_declares():
    assert parse(GERMAN_COPYBOOK).record_length() == 60


def test_the_zone_nibble_carries_the_sign_in_every_page():
    for page in ("cp037", "cp273", "cp500", "cp1026"):
        raw = encode_overpunch_bytes("0000012340", negative=True, encoding=page)
        assert zone_sign(raw) == (-1, "0"), page
        raw = encode_overpunch_bytes("0000012347", negative=False, encoding=page)
        assert zone_sign(raw) == (1, "7"), page


def test_the_character_the_sign_byte_decodes_to_is_not_portable():
    """The reason the sign cannot be read from text."""
    assert bytes([0xD0]).decode("cp037") == "}"
    assert bytes([0xD0]).decode("cp273") == "ü"
    assert bytes([0xD0]).decode("cp1026") == "ğ"


@pytest.mark.parametrize("page", ["cp037", "cp273", "cp500", "cp1026"])
def test_a_minus_zero_overpunch_decodes_on_every_page(page):
    """-0 is the case that broke: it is the only digit whose sign byte differs
    between code pages, so 90% of negatives decoded fine and 10% did not."""
    pic = parse("000100 01  R.\n000200     05  A  PIC S9(06)V99.\n").find("A").pic
    raw = encode_overpunch_bytes("00123450", negative=True, encoding=page)
    assert decode_display(raw, pic, encoding=page) == Decimal("-1234.50")


def test_the_numbers_are_identical_whatever_page_you_read_it_with(tmp_path):
    """Text differs between code pages. Money must not."""
    layout = parse(GERMAN_COPYBOOK)
    path, negatives, total = german_file(tmp_path)
    results = {}
    for page in ("cp273", "cp037", "cp500", "cp1026"):
        st = scan(path, layout, encoding=page)["KONTOSTAND"]
        results[page] = (st.overpunch_negative, st.sum_correct)
    assert len(set(results.values())) == 1, results
    assert results["cp273"] == (negatives, total)


def test_the_finding_reports_the_true_total(tmp_path):
    layout = parse(GERMAN_COPYBOOK)
    path, negatives, total = german_file(tmp_path)
    findings = evaluate(layout, scan(path, layout, encoding="cp273"))
    sign = next(f for f in findings if f.code == "TRAILING_SIGN")
    assert sign.evidence["negative"] == f"{negatives:,}"
    assert f"{total:,.2f}" in sign.impact


def test_german_field_names_survive_parsing():
    layout = parse(GERMAN_COPYBOOK)
    names = {f.name for f in layout.elementary_fields()}
    assert {"KUNDEN-NR", "GEBURTSDATUM", "BEARBEITER-KUERZEL"} <= names
    assert layout.find("STATUS-KZ").conditions == {"KUNDE-AKTIV": ["A"],
                                                   "KUNDE-GESPERRT": ["G"]}
