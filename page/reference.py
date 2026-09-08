"""Emit what the Python implementation says, so the JavaScript can be checked.

Run this, then `node page/verify_js.js`. The two implementations are written
separately on purpose: where they disagree, one of them is wrong, and finding
out which is cheaper than either of them being wrong quietly.
"""
from __future__ import annotations
import base64, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from overpunch.copybook import parse_file          # noqa: E402
from overpunch.findings import evaluate            # noqa: E402
from overpunch.probe import scan                   # noqa: E402
from overpunch.plan import build as build_plan     # noqa: E402
from overpunch.generate import postgres_ddl        # noqa: E402

PAIRS = [
    ("demo/UTLBILL.cpy", "demo/UTLBILL.dat"),
    ("demo/carddemo/CVTRA06Y.cpy", "demo/carddemo/DALYTRAN.PS"),
    ("demo/carddemo/CVACT01Y.cpy", "demo/carddemo/ACCTDATA.PS"),
    ("demo/carddemo/CVCUS01Y.cpy", "demo/carddemo/CUSTDATA.PS"),
    ("demo/carddemo/CVACT02Y.cpy", "demo/carddemo/CARDDATA.PS"),
    # a RECFM=VB file, so the comparison actually exercises descriptor words
    ("samples/data/variable-blocked.cpy", "samples/data/variable-blocked.dat"),
    # a numeric field left as low-values, which is how a mainframe says "unset".
    # Without this the two implementations disagreed on a real file: one counted
    # every non-digit final byte as a sign, the other only real sign characters.
    ("samples/data/unset-numeric.cpy", "samples/data/unset-numeric.dat"),
    ("samples/data/range-condition.cpy", "samples/data/range-condition.dat"),
    # ASCII code pages, which every case above leaves untested. The zoned one is
    # the third sign convention - 0x70-0x79 - and the browser copy has to select
    # it from the code page exactly as the Python does, or the two implementations
    # agree on EBCDIC and quietly disagree on every PC-COBOL extract.
    ("samples/data/ascii-separate-sign.cpy", "samples/data/ascii-separate-sign.dat",
     "latin-1"),
    ("samples/data/ascii-native-zoned.cpy", "samples/data/ascii-native-zoned.dat",
     "latin-1"),
    # ...and the SAME bytes declared as EBCDIC, which is the discrimination case:
    # both implementations must refuse it with UNKNOWN_SIGN_BYTE rather than
    # inventing 60 negatives. Without this the corpus contains no file that makes
    # the new rule fire at all, and a rule no case reaches is not cross-checked.
    ("samples/data/ascii-native-zoned.cpy", "samples/data/ascii-native-zoned.dat",
     "cp037"),
    # COMP-1/COMP-2, which no other case carries with data behind it. The pair is
    # the point: same numbers, same copybook, one written as IBM hex float and one
    # as IEEE 754, and the browser has to reach the same verdict as the Python on
    # both. Without these the float rules are cross-checked by nothing at all.
    ("samples/data/float-hex.cpy", "samples/data/float-hex.dat"),
    ("samples/data/float-ieee.cpy", "samples/data/float-ieee.dat"),
    # ...and the byte order, which is a second unknown the browser must also get
    # right. A real compiler (GnuCOBOL on x86) writes this one.
    ("samples/data/float-ieee-le.cpy", "samples/data/float-ieee-le.dat"),
    # COMP-5: the width, which the JS parser has to agree on before anything
    # else can be compared, and the two tells.
    ("samples/data/comp5-fullrange.cpy", "samples/data/comp5-fullrange.dat"),
    ("samples/data/comp5-little.cpy", "samples/data/comp5-little.dat"),
    # a coded field unset in SOME records: the only input that separates
    # MOSTLY_UNSET from NEVER_POPULATED, and the corpus had none.
    ("samples/data/partly-unset.cpy", "samples/data/partly-unset.dat"),
]
LIMIT = 300


def main() -> None:
    out = {"cp037": "".join(bytes([i]).decode("cp037") for i in range(256)),
           "cases": []}
    # a German case, so the comparison actually exercises a non-US code page.
    # Without one it agrees on everything and discriminates nothing.
    out["pages"] = {p: "".join(bytes([i]).decode(p) for i in range(256))
                    for p in ("cp037", "cp273", "cp500", "cp1026", "latin-1")}
    for pair in PAIRS:
        cpy, dat = pair[0], pair[1]
        enc = pair[2] if len(pair) > 2 else "cp037"
        cp, dp = ROOT / cpy, ROOT / dat
        if not (cp.exists() and dp.exists()):
            # A missing pair used to be skipped in silence, so a cold clone
            # compared 11 files instead of 12 and verify_js still reported
            # "0 disagreements" - a green cross-check over less than the corpus,
            # which is the one thing this file exists to prevent.
            missing = cpy if not cp.exists() else dat
            out.setdefault("skipped", []).append(missing)
            continue
        layout = parse_file(str(cp))
        stats = scan(str(dp), layout, encoding=enc, limit=LIMIT)
        raw = dp.read_bytes()[:LIMIT * layout.record_length()]
        out["cases"].append({
            "name": cp.name if enc == "cp037" else f"{cp.name} ({enc})",
            "copybook": cp.read_text(),
            "data": base64.b64encode(raw).decode(),
            "encoding": enc,
            "record_len": layout.record_length(),
            # how many records were actually READ. Comparing findings alone let a
            # reader that returned nothing agree with one that read 30, because
            # both produce an empty finding list.
            "records": next(iter(stats.values())).examined if stats else 0,
            "fields": [{"name": f.name, "offset": f.offset,
                        "len": f.total_size(), "usage": f.usage.value}
                       for f in layout.elementary_fields()],
            "findings": [{"code": f.code, "severity": f.severity,
                          "field": f.field, "impact": f.impact}
                         for f in evaluate(layout, stats)],
        })
    out["ddl_cases"] = []
    answers = {"REDEFINES_BRANCH": {"discriminator": "TR-DISCRIMINATOR",
                                    "map": {"P": "TR-PAYLOAD-PERSON"}}}
    for cpy, keys in (("demo/TORTURE.cpy", ["TR-ACCOUNT"]),
                      ("demo/TORTURE.cpy", ["TR-REGION", "TR-ACCOUNT"]),
                      ("demo/UTLBILL.cpy", []),
                      ("demo/carddemo/CVTRA06Y.cpy", ["DALYTRAN-ID"])):
        path = ROOT / cpy
        if not path.exists():
            continue
        layout = parse_file(str(path))
        layout.source_name = f"demo/{path.name}"
        plan = build_plan(layout, encoding="cp037")
        for u in plan["unresolved"]:
            if u["kind"] == "REDEFINES_BRANCH":
                u["resolution"] = answers["REDEFINES_BRANCH"]
            elif u["kind"] == "PRIMARY_KEY":
                u["resolution"] = {"primary_key": keys}
            else:
                u["resolution"] = {"noted": True}
        out["ddl_cases"].append({"name": f"{path.name} pk={keys or 'none'}",
                                 "plan": plan, "keys": keys,
                                 "ddl": postgres_ddl(plan)})

    import sys as _sys
    _sys.path.insert(0, str(ROOT / "tests"))
    from test_codepages import GERMAN_COPYBOOK, german_file      # noqa: E402
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        gpath, _, _ = german_file(Path(td), "cp273", records=120)
        glayout = parse_file(str(ROOT / "demo" / "UTLBILL.cpy"))  # placeholder
        from overpunch.copybook import parse as parse_src
        glayout = parse_src(GERMAN_COPYBOOK, source_name="KUNDE.cpy")
        graw = Path(gpath).read_bytes()
        gstats = scan(gpath, glayout, encoding="cp273")
        out["cases"].append({
            "name": "KUNDE.cpy (cp273)",
            "copybook": GERMAN_COPYBOOK,
            "data": base64.b64encode(graw).decode(),
            "encoding": "cp273",
            "record_len": glayout.record_length(),
            "fields": [{"name": f.name, "offset": f.offset, "len": f.total_size(),
                        "usage": f.usage.value} for f in glayout.elementary_fields()],
            "findings": [{"code": f.code, "severity": f.severity, "field": f.field,
                          "impact": f.impact}
                         for f in evaluate(glayout, gstats)],
        })

    # the copybook conventions that broke both parsers, as synthetic fixtures
    _sys.path.insert(0, str(ROOT / "tests"))
    from test_dialects import (DECIMAL_IN_VALUE, EXTERNAL_REDEFINES,  # noqa: E402
                               FIVE_DIGIT_SEQ, FRAGMENT, WITH_DIRECTIVES)
    ELEMENTARY_OCCURS = ("000100 01  REC.\n"
                         "000200     05  BEFORE     PIC X(02).\n"
                         "000300     05  LINE-ITEM  PIC X(40) OCCURS 5 TIMES.\n"
                         "000400     05  AFTER      PIC X(03).\n")
    from overpunch.copybook import parse as _parse                    # noqa: E402
    out["dialects"] = []
    for name, text in (("five-digit sequence", FIVE_DIGIT_SEQ),
                       ("listing directives", WITH_DIRECTIVES),
                       ("fragment, no 01", FRAGMENT),
                       ("decimal point in VALUE", DECIMAL_IN_VALUE),
                       ("redefines an external record", EXTERNAL_REDEFINES),
                       ("OCCURS on an elementary field", ELEMENTARY_OCCURS)):
        lay = _parse(text)
        out["dialects"].append({
            "name": name, "copybook": text,
            "record_len": lay.record_length(),
            "fields": [{"name": f.name, "offset": f.offset,
                        "len": f.total_size(), "usage": f.usage.value}
                       for f in lay.elementary_fields()]})

    # every field in the repository, with the type and identifier the Python
    # side gives it, so the browser's copy of that mapping can be held to it
    from overpunch.plan import normalise as _norm, sql_type, DEFAULT_POLICIES
    import glob as _glob
    types, type_sources = [], {}
    for cpy in sorted(_glob.glob(str(ROOT / "demo" / "*.cpy")) +
                      _glob.glob(str(ROOT / "demo" / "carddemo" / "*.cpy")) +
                      _glob.glob(str(ROOT / "samples" / "data" / "*.cpy"))):
        lay = parse_file(cpy)
        type_sources[Path(cpy).name] = Path(cpy).read_text(errors="replace")
        for f in lay.elementary_fields():
            t, _note = sql_type(f, DEFAULT_POLICIES)
            types.append({"copybook": Path(cpy).name, "field": f.name,
                          "ident": _norm(f.name), "type": t})
    out["types"] = types
    out["type_sources"] = type_sources

    # The rule SET, not just the findings a corpus happens to produce. A rule
    # present in one implementation and absent from the other is invisible to a
    # finding-by-finding comparison unless some case triggers it - and
    # MOSTLY_UNSET hid that way from the day it was written until 2026-09-08,
    # because no reference file had a partly-unset coded field. This check needs
    # no corpus case at all.
    import re as _re
    findings_src = (ROOT / "src" / "overpunch" / "findings.py").read_text()
    out["rule_codes"] = sorted(set(_re.findall(r'code="([A-Z][A-Z_]+)"', findings_src)))

    dest = ROOT / "page" / "reference.json"
    dest.write_text(json.dumps(out))
    print(f"wrote {dest.relative_to(ROOT)} - {len(out['cases'])} cases, "
          f"{len(out['rule_codes'])} rules")
    for miss in out.get("skipped", []):
        print(f"  !! SKIPPED {miss} - not on disk; the cross-check below will "
              f"compare LESS than the full corpus")
    for c in out["cases"]:
        print(f"  {c['name']:<16} {c['record_len']:>4}B  "
              f"{len(c['fields']):>2} fields  {len(c['findings']):>2} findings")
    print(f"  {len(out['types'])} field type mappings")
    for c in out["dialects"]:
        print(f"  dialect {c['name']:<32} {c['record_len']:>4}B  "
              f"{len(c['fields'])} fields")
    for c in out["ddl_cases"]:
        print(f"  DDL {c['name']:<38} {len(c['ddl'].splitlines()):>3} lines")


if __name__ == "__main__":
    main()
