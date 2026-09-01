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
]
LIMIT = 300


def main() -> None:
    out = {"cp037": "".join(bytes([i]).decode("cp037") for i in range(256)),
           "cases": []}
    # a German case, so the comparison actually exercises a non-US code page.
    # Without one it agrees on everything and discriminates nothing.
    out["pages"] = {p: "".join(bytes([i]).decode(p) for i in range(256))
                    for p in ("cp037", "cp273", "cp500", "cp1026")}
    for cpy, dat in PAIRS:
        cp, dp = ROOT / cpy, ROOT / dat
        if not (cp.exists() and dp.exists()):
            continue
        layout = parse_file(str(cp))
        stats = scan(str(dp), layout, limit=LIMIT)
        raw = dp.read_bytes()[:LIMIT * layout.record_length()]
        out["cases"].append({
            "name": cp.name,
            "copybook": cp.read_text(),
            "data": base64.b64encode(raw).decode(),
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

    dest = ROOT / "page" / "reference.json"
    dest.write_text(json.dumps(out))
    print(f"wrote {dest.relative_to(ROOT)} - {len(out['cases'])} cases")
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
