"""Conventions real copybooks use that this parser did not survive.

Every case here was found by pointing the tool at twelve production copybooks
from a government tax system and watching all twelve fail. Nine returned a
ZERO-byte record with no error; three raised.

The fixtures are written from scratch. None of that source appears here.
"""

from __future__ import annotations

import pytest

from overpunch.copybook import CopybookError, detect_format, parse
from overpunch.layout import LayoutError

# --- 1. the sequence area is not always six digits --------------------------

FIVE_DIGIT_SEQ = """\
00001 ***** a comment, indicator in column 7
00002 *     another one
00003     05  ACCT-ID              PIC 9(08).
00004     05  ACCT-NAME            PIC X(20).
00005     05  ACCT-BALANCE         PIC S9(07)V99 COMP-3.
"""


def test_a_five_digit_sequence_number_is_still_fixed_format():
    """`00001 ` is as ordinary as `000100`, and insisting on six digits is what
    made every comment parse as a statement."""
    assert detect_format(FIVE_DIGIT_SEQ) == "fixed"


def test_the_comments_do_not_become_fields():
    layout = parse(FIVE_DIGIT_SEQ)
    names = {f.name for f in layout.elementary_fields()}
    assert names == {"ACCT-ID", "ACCT-NAME", "ACCT-BALANCE"}
    assert layout.record_length() == 8 + 20 + 5


def test_the_failure_it_replaces_was_silent():
    """The old behaviour: `00001` read as a level number, the first comment
    became the 01 record, comments carry no PICTURE, so the record was zero
    bytes long and nothing was raised. A test that only checked for an
    exception would have passed."""
    layout = parse(FIVE_DIGIT_SEQ)
    assert layout.record_length() > 0
    assert layout.elementary_fields()


def test_free_format_is_still_detected_as_free():
    free = "01  REC.\n    05  A  PIC X(03).\n        88  IS-A  VALUE 'A'.\n"
    assert detect_format(free) == "free"
    assert parse(free).record_length() == 3


# --- 2. compiler directives -------------------------------------------------

WITH_DIRECTIVES = """\
00001     SKIP1
00002     05  FIRST-FIELD          PIC X(04).
00003     SKIP2
00004     05  SECOND-FIELD         PIC X(06).
00005     EJECT
00006     05  THIRD-FIELD          PIC X(02).
"""


def test_listing_directives_do_not_swallow_the_next_field():
    """SKIP1 and EJECT carry no terminating period. Left in place they merge
    with the following line, and the field after each one disappears."""
    layout = parse(WITH_DIRECTIVES)
    assert [f.name for f in layout.elementary_fields()] == \
           ["FIRST-FIELD", "SECOND-FIELD", "THIRD-FIELD"]
    assert layout.record_length() == 12


# --- 3. a copybook with no 01 level -----------------------------------------

FRAGMENT = """\
00001     10  PART-ONE             PIC X(05).
00002     10  PART-TWO             PIC 9(03).
"""


def test_a_fragment_without_an_01_level_is_accepted():
    """Most copybooks are fragments: a group of fields meant to be COPY'd into a
    record the program declares. All twelve real ones started at 05 or 10.
    Requiring 01 rejects the common case."""
    layout = parse(FRAGMENT)
    assert layout.is_fragment
    assert layout.record_length() == 8
    assert [f.name for f in layout.elementary_fields()] == ["PART-ONE", "PART-TWO"]


def test_a_copybook_with_an_01_is_not_marked_a_fragment():
    assert not parse("000100 01  R.\n000200     05  A  PIC X(03).\n").is_fragment


# --- 4. a period is only a terminator when a space follows it ---------------

DECIMAL_IN_VALUE = """\
00001     10  ADJUSTMENT           PIC S9(07)V99 COMP-3.
00002         88  ADJ-IS-NULL          VALUE -9999999.99.
00003     10  TRAILING-FIELD       PIC X(04).
"""


def test_a_decimal_point_inside_a_value_does_not_end_the_statement():
    """`VALUE -9999999.99.` was split at the DECIMAL POINT: the statement ended
    early and `99` was parsed as a level-99 field nested under a field that
    already had a PICTURE."""
    layout = parse(DECIMAL_IN_VALUE)
    assert [f.name for f in layout.elementary_fields()] == \
           ["ADJUSTMENT", "TRAILING-FIELD"]
    assert layout.find("ADJUSTMENT").conditions == {"ADJ-IS-NULL": ["-9999999.99"]}
    assert layout.record_length() == 5 + 4


def test_a_period_inside_a_quoted_literal_does_not_end_the_statement():
    layout = parse("00001     10  TITLE-CODE   PIC X(04).\n"
                   "00002         88  IS-DOCTOR    VALUE 'DR. '.\n"
                   "00003     10  NEXT-FIELD   PIC X(02).\n")
    assert [f.name for f in layout.elementary_fields()] == ["TITLE-CODE", "NEXT-FIELD"]
    assert layout.record_length() == 6


# --- 5. redefining something declared in another copybook -------------------

EXTERNAL_REDEFINES = """\
00001     05  MY-VIEW REDEFINES SOMETHING-DECLARED-ELSEWHERE.
00002         10  VIEW-ID          PIC 9(06).
00003         10  VIEW-NAME        PIC X(10).
"""


def test_redefining_a_target_that_is_not_here_still_has_a_length():
    """A copybook may redefine a record laid out in a different copybook. There
    is nothing here to share bytes with, so the redefinition IS the record -
    treating it as a shadow gave a zero-byte layout that then failed validation."""
    layout = parse(EXTERNAL_REDEFINES)
    assert layout.record_length() == 16
    assert layout.external_redefines() == [("MY-VIEW", "SOMETHING-DECLARED-ELSEWHERE")]


def test_an_internal_redefines_still_shares_its_bytes():
    """The change must not turn every REDEFINES into extra length."""
    layout = parse("00001     05  A            PIC X(09).\n"
                   "00002     05  A-PARTS REDEFINES A.\n"
                   "00003         10  A-1      PIC X(04).\n"
                   "00004         10  A-2      PIC X(05).\n"
                   "00005     05  B            PIC X(02).\n")
    assert layout.record_length() == 11
    assert layout.external_redefines() == []
    assert layout.find("A-1").offset == 0
    assert layout.find("B").offset == 9


# --- 6. more than one record in one copybook --------------------------------

TWO_RECORDS = """\
00001 01  HEADER-RECORD.
00002     05  HDR-TYPE             PIC X(02).
00003     05  HDR-DATE             PIC 9(08).
00004 01  DETAIL-RECORD.
00005     05  DTL-ID               PIC 9(06).
00006     05  DTL-AMOUNT           PIC S9(07)V99.
00007     05  DTL-FLAG             PIC X(01).
"""


def test_a_second_01_starts_a_new_record_rather_than_orphaning():
    """An 01 is a record boundary. Treating the second as a field under the
    first raised 'orphaned level 1' and lost everything after it - which is what
    happened on 65 of 885 real copybooks, one of them declaring 24 records."""
    from overpunch.copybook import parse_records
    records = parse_records(TWO_RECORDS)
    assert [r.root.name for r in records] == ["HEADER-RECORD", "DETAIL-RECORD"]
    assert records[0].record_length() == 10
    assert records[1].record_length() == 6 + 9 + 1


def test_parse_returns_the_first_and_names_the_rest():
    layout = parse(TWO_RECORDS)
    assert layout.root.name == "HEADER-RECORD"
    assert layout.other_records == ["DETAIL-RECORD"]


def test_a_single_record_copybook_names_no_others():
    assert parse(FRAGMENT).other_records == []


# --- 7. a COPY member that is procedure code, not a layout ------------------

PROCEDURE_CODE = """\
00001 S3100-CONSTRUCT.
00002     MOVE LOW-VALUES TO WORK-AREA.
00003     IF SOME-CONDITION
00004         PERFORM S3200-DO-IT
00005     ELSE
00006         GO TO S3100-EXIT.
"""


def test_procedure_code_is_named_as_such_not_reported_as_a_parse_failure():
    """24 of 885 members hold executable statements. 'no 01-level record found'
    sounds like the parser broke; it did not, there is simply nothing here to
    decode a data file with."""
    with pytest.raises(CopybookError) as exc:
        parse(PROCEDURE_CODE)
    assert "procedure-division code" in str(exc.value)
    assert "no PICTURE clauses" in str(exc.value)


# --- 8. variable-length records with a descriptor word ----------------------

VB_CPY = "000100 01  REC.\n000200     05  BODY  PIC X(70).\n"


def vb_file(tmp_path, records=779, body_len=70):
    """RECFM=VB: a 4-byte RDW per record - big-endian length INCLUDING the RDW,
    then two reserved zero bytes."""
    path = tmp_path / "vb.dat"
    with open(path, "wb") as fh:
        for i in range(records):
            body = f"RECORD{i:06d}".ljust(body_len).encode("cp037")
            fh.write((body_len + 4).to_bytes(2, "big") + b"\x00\x00" + body)
    return path


def test_a_vb_file_is_detected_and_read(tmp_path):
    """A 70-byte record with a 4-byte descriptor is 74 on disk, which is why
    the file divides by 74 and not by 70."""
    from overpunch.probe import detect_recfm, iter_records
    path = vb_file(tmp_path)
    assert path.stat().st_size == 779 * 74
    assert detect_recfm(str(path), 70) == "vb"
    records = list(iter_records(str(path), 70))
    assert len(records) == 779
    assert all(len(r) == 70 for r in records)
    assert records[0].decode("cp037").strip() == "RECORD000000"


def test_a_fixed_file_is_not_mistaken_for_vb(tmp_path):
    """Divisibility alone is not the test: the descriptor word has to be real."""
    from overpunch.probe import detect_recfm
    path = tmp_path / "fixed.dat"
    path.write_bytes(("X" * 70).encode("cp037") * 100)
    assert detect_recfm(str(path), 70) == "fixed"


def test_a_broken_descriptor_word_is_reported_not_decoded(tmp_path):
    """Once the reader is out of step every later record is nonsense, so it must
    stop rather than produce plausible garbage."""
    from overpunch.probe import VariableRecordError, iter_records
    path = vb_file(tmp_path, records=5)
    raw = bytearray(path.read_bytes())
    raw[2 * 74 + 2] = 0x07                    # corrupt the third RDW's reserved bytes
    path.write_bytes(bytes(raw))
    with pytest.raises(VariableRecordError) as exc:
        list(iter_records(str(path), 70))
    assert "record descriptor word" in str(exc.value)


def test_the_mismatch_message_names_the_descriptor_word():
    """What the screenshot needed: not 'it does not divide', but why."""
    from overpunch.probe import explain_mismatch
    notes = " ".join(explain_mismatch(57_646, 70))
    assert "70 + 4 = 74" in notes
    assert "779 records" in notes
    assert "RECFM=VB" in notes


def test_the_mismatch_message_lists_what_could_divide():
    from overpunch.probe import explain_mismatch
    notes = " ".join(explain_mismatch(57_646, 58))
    assert "74" in notes and "82" in notes
