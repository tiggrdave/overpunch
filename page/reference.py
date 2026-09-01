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
    dest = ROOT / "page" / "reference.json"
    dest.write_text(json.dumps(out))
    print(f"wrote {dest.relative_to(ROOT)} - {len(out['cases'])} cases")
    for c in out["cases"]:
        print(f"  {c['name']:<16} {c['record_len']:>4}B  "
              f"{len(c['fields']):>2} fields  {len(c['findings']):>2} findings")


if __name__ == "__main__":
    main()
