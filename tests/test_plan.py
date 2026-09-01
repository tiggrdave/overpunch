"""The plan, and the refusal.

The point of a plan is that the decisions a copybook cannot settle are visible
and answered by a person. So the tests that matter are the ones asserting the
tool STOPS - and that what it emits afterwards is real SQL, checked by handing
it to an actual SQL parser rather than by reading it.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from overpunch.copybook import parse, parse_file
from overpunch.generate import json_schema, loader_script, postgres_ddl
from overpunch.plan import PlanError, build, normalise, open_decisions

CPY = str(Path(__file__).resolve().parents[1] / "demo" / "TORTURE.cpy")


@pytest.fixture
def plan():
    return build(parse_file(CPY))


@pytest.fixture
def resolved(plan):
    for u in plan["unresolved"]:
        if u["kind"] == "REDEFINES_BRANCH":
            u["resolution"] = {"discriminator": "TR-DISCRIMINATOR",
                               "map": {"P": "TR-PAYLOAD-PERSON",
                                       "L": "TR-PAYLOAD-POLICY"}}
        elif u["kind"] == "PRIMARY_KEY":
            u["resolution"] = {"primary_key": ["TR-ACCOUNT"]}
    return plan


# --- what the tool refuses to decide ---------------------------------------

def test_the_plan_stops_on_the_decisions_a_copybook_cannot_settle(plan):
    kinds = {u["kind"] for u in open_decisions(plan)}
    assert kinds == {"REDEFINES_BRANCH", "PRIMARY_KEY"}


def test_redefined_bytes_do_not_become_columns_by_default(plan):
    """The same 20 bytes are a person OR a policy. Emitting both as populated
    columns would be a guess wearing a schema."""
    names = {c["field"] for t in plan["tables"] for c in t["columns"]}
    assert "TR-PAYLOAD" not in names
    assert "TR-P-SURNAME" not in names
    assert "TR-P-NUMBER" not in names


def test_generation_is_refused_while_a_decision_is_open(plan):
    with pytest.raises(PlanError) as exc:
        postgres_ddl(plan)
    assert "still open" in str(exc.value)


def test_the_redefines_question_is_asked_not_answered(plan):
    """The template offers a placeholder, never a guessed discriminator.

    TR-DISCRIMINATOR is obvious to a human reading the copybook. It is not
    derivable from it, and a tool that filled it in would be right here and
    wrong on the next file, with no way to tell the two apart.
    """
    u = next(u for u in plan["unresolved"] if u["kind"] == "REDEFINES_BRANCH")
    assert u["resolve_with"]["discriminator"] == "<FIELD-NAME>"
    assert u["resolution"] is None


def test_the_refusal_says_how_to_answer(plan):
    with pytest.raises(PlanError) as exc:
        postgres_ddl(plan)
    assert "resolution" in str(exc.value)
    assert "discriminator" in str(exc.value)


# --- what it emits once answered -------------------------------------------

def test_the_ddl_is_accepted_by_a_real_sql_parser(resolved):
    """Handed to sqlite rather than read by eye.

    An earlier version put the column-separating comma AFTER the trailing
    comment, so the comma was commented out and the DDL could not parse. It
    looked completely fine in a terminal.
    """
    con = sqlite3.connect(":memory:")
    con.executescript(postgres_ddl(resolved))
    tables = {r[0] for r in con.execute(
        "select name from sqlite_master where type='table'")}
    assert tables == {"torture_record", "torture_record_tr_quarters",
                      "torture_record_tr_entries"}


def test_every_column_survives_the_round_trip_into_the_database(resolved):
    """Compared against the plan exactly, not against a hand-picked sample.

    A missing separator does not necessarily fail to parse: SQLite accepts
    almost any token sequence as a type name, so a swallowed comma silently
    fuses two columns into one with a nonsense type. The table is still created
    and a spot-check of a few column names still passes. Only the full set,
    counted, tells you a column went missing.
    """
    con = sqlite3.connect(":memory:")
    con.executescript(postgres_ddl(resolved))
    for t in resolved["tables"]:
        want = [c["name"] for c in t["columns"]]
        if t["kind"] == "occurs":
            want = ["parent_id", "occurrence"] + want
        got = [r[1] for r in con.execute(f"pragma table_info({t['name']})")]
        assert got == want, (
            f"{t['name']}: expected {len(want)} columns, database has "
            f"{len(got)}\n  expected: {want}\n  got     : {got}")


def test_no_column_type_swallowed_a_neighbouring_column(resolved):
    """The direct assertion: a type is never another column's name."""
    con = sqlite3.connect(":memory:")
    con.executescript(postgres_ddl(resolved))
    for t in resolved["tables"]:
        names = {c["name"] for c in t["columns"]}
        for row in con.execute(f"pragma table_info({t['name']})"):
            declared = row[2].lower()
            for other in names:
                assert other not in declared.split(), (
                    f"{t['name']}.{row[1]} has type {row[2]!r}, which contains "
                    f"the column name {other!r} - a separator was lost")


def test_a_repeating_group_becomes_a_child_table_keyed_to_its_parent(resolved):
    child = next(t for t in resolved["tables"] if t["occurs_of"] == "TR-QUARTERS")
    assert child["kind"] == "occurs" and child["occurs_max"] == 4
    con = sqlite3.connect(":memory:")
    con.executescript(postgres_ddl(resolved))
    cols = [r[1] for r in con.execute("pragma table_info(torture_record_tr_quarters)")]
    assert cols[:2] == ["parent_id", "occurrence"]


def test_the_variable_group_records_what_it_depends_on(resolved):
    child = next(t for t in resolved["tables"] if t["occurs_of"] == "TR-ENTRIES")
    assert child["depends_on"] == "TR-ENTRY-COUNT"
    assert "DEPENDING ON TR-ENTRY-COUNT" in postgres_ddl(resolved)


def test_flattening_is_a_policy_not_a_rewrite():
    p = build(parse_file(CPY), policies={"occurs": "flatten"})
    names = {c["name"] for t in p["tables"] for c in t["columns"]}
    assert {"tr_q_amt_1", "tr_q_amt_2", "tr_q_amt_3", "tr_q_amt_4"} <= names
    assert not any(t["kind"] == "occurs" for t in p["tables"])


# --- type mapping -----------------------------------------------------------

def test_types_carry_the_precision_the_picture_declared(resolved):
    by = {c["field"]: c["type"] for t in resolved["tables"] for c in t["columns"]}
    assert by["TR-SIGNED-DISPLAY"] == "NUMERIC(9,2)"   # S9(07)V99
    assert by["TR-PACKED"] == "NUMERIC(11,2)"          # S9(09)V99 COMP-3
    assert by["TR-BIN-DOUBLE"] == "BIGINT"             # S9(18) COMP
    assert by["TR-BIN-HALF"] == "SMALLINT"             # S9(04) COMP
    assert by["TR-REGION"] == "VARCHAR(2)"


def test_the_float_encoding_it_could_not_determine_is_written_down(resolved):
    """Nothing in the bytes says IBM hex or IEEE. The default is recorded as a
    default, on the column, rather than being quietly applied."""
    col = next(c for t in resolved["tables"] for c in t["columns"]
               if c["field"] == "TR-FLOAT-SHORT")
    assert "ibm_hex" in col["note"]
    assert "float_encoding=ibm_hex" in postgres_ddl(resolved)


def test_digits_beyond_bigint_do_not_silently_become_bigint():
    p = build(parse("000100 01  R.\n000200     05  BIG  PIC S9(20).\n"))
    col = p["tables"][0]["columns"][0]
    assert col["type"] == "NUMERIC(20)" and "exceeds BIGINT" in col["note"]


# --- identifiers ------------------------------------------------------------

def test_cobol_names_become_legal_identifiers():
    assert normalise("TR-E-CODE") == "tr_e_code"
    assert normalise("9-LIVES") == "c_9_lives"


def test_a_name_collision_is_raised_rather_than_letting_one_win():
    p = build(parse("000100 01  R.\n"
                    "000200     05  A-B  PIC X(01).\n"
                    "000300     05  A_B  PIC X(01).\n"))
    kinds = {u["kind"] for u in open_decisions(p)}
    assert "IDENTIFIER_COLLISION" in kinds


# --- other targets ----------------------------------------------------------

def test_json_schema_carries_the_byte_provenance(resolved):
    doc = json.loads(json_schema(resolved))
    prop = doc["properties"]["tr_signed_display"]
    assert prop["type"] == "number"
    assert prop["x-cobol-field"] == "TR-SIGNED-DISPLAY"
    assert prop["x-offset"] == 12
    assert doc["properties"]["tr_quarters"]["type"] == "array"


def test_json_schema_is_also_refused_while_open(plan):
    with pytest.raises(PlanError):
        json_schema(plan)


def test_the_loader_names_the_table_and_its_columns(resolved):
    sh = loader_script(resolved)
    assert "torture_record" in sh and "tr_region" in sh
    assert "overpunch decode" in sh


# --- names in languages other than English ----------------------------------

def test_accented_names_are_transliterated_not_mangled():
    """COBOL keywords are English; field names are not.

    Stripping anything outside a-z turned GEBÜRTSDATUM into 'geb_rtsdatum' and
    dropped the leading character of ÖZDEMIR-KODU entirely - a mangled name, and
    a new source of collisions between names differing only in their accents.
    """
    assert normalise("GEBÜRTSDATUM") == "gebuertsdatum"
    assert normalise("ÖZDEMIR-KODU") == "oezdemir_kodu"
    assert normalise("MONTANT-RÉGLÉ") == "montant_regle"
    assert normalise("WEIß-BETRAG") == "weiss_betrag"
    assert normalise("FORSIKRING-BELØB") == "forsikring_beloeb"


def test_transliteration_keeps_names_distinct():
    """Two German names differing only in an umlaut must not collapse together."""
    assert normalise("SCHULE") != normalise("SCHÜLE")


def test_plain_ascii_names_are_untouched():
    assert normalise("DALYTRAN-MERCHANT-ID") == "dalytran_merchant_id"
    assert normalise("TR-E-CODE") == "tr_e_code"
