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

    dest = ROOT / "page" / "reference.json"
    dest.write_text(json.dumps(out))
    print(f"wrote {dest.relative_to(ROOT)} - {len(out['cases'])} cases")
    for c in out["cases"]:
        print(f"  {c['name']:<16} {c['record_len']:>4}B  "
              f"{len(c['fields']):>2} fields  {len(c['findings']):>2} findings")
    for c in out["ddl_cases"]:
        print(f"  DDL {c['name']:<38} {len(c['ddl'].splitlines()):>3} lines")


if __name__ == "__main__":
    main()
