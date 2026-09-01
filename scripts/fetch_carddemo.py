"""Fetch AWS CardDemo - a real, public mainframe application - as a test case.

Everything else in this repository is synthetic by design. This is the opposite:
a COBOL application published by AWS, with genuine EBCDIC data files and the
copybooks that describe them, written by people who had never heard of this tool.

Nothing is vendored. The files are downloaded on demand into demo/carddemo/,
which is gitignored, so this repository redistributes none of it.

    python scripts/fetch_carddemo.py
    overpunch scan demo/carddemo/CVTRA06Y.cpy demo/carddemo/DALYTRAN.PS

Source: https://github.com/aws-samples/aws-mainframe-modernization-carddemo
Licence: Apache-2.0
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

BASE = ("https://raw.githubusercontent.com/aws-samples/"
        "aws-mainframe-modernization-carddemo/main")
OUT = Path(__file__).resolve().parents[1] / "demo" / "carddemo"

COPYBOOKS = ["CVACT01Y", "CVACT02Y", "CVACT03Y", "CVCUS01Y", "CVTRA01Y",
             "CVTRA05Y", "CVTRA06Y", "CUSTREC"]
DATA = {"ACCTDATA.PS": "AWS.M2.CARDDEMO.ACCTDATA.PS",
        "CUSTDATA.PS": "AWS.M2.CARDDEMO.CUSTDATA.PS",
        "CARDDATA.PS": "AWS.M2.CARDDEMO.CARDDATA.PS",
        "DALYTRAN.PS": "AWS.M2.CARDDEMO.DALYTRAN.PS"}

# What the copybooks' own comments claim, so the download can be checked against
# something written by the source rather than by us.
DECLARED_LENGTHS = {"CVACT01Y": 300, "CVTRA06Y": 350}


def get(url: str, dest: Path) -> int:
    with urllib.request.urlopen(url, timeout=60) as r:
        body = r.read()
    dest.write_bytes(body)
    return len(body)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in COPYBOOKS:
        n = get(f"{BASE}/app/cpy/{name}.cpy", OUT / f"{name}.cpy")
        print(f"  {name}.cpy{'':<6} {n:>8,} bytes")
    for local, remote in DATA.items():
        n = get(f"{BASE}/app/data/EBCDIC/{remote}", OUT / local)
        print(f"  {local:<14} {n:>8,} bytes")

    print("\nchecking each copybook against the file it should describe:")
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from overpunch.copybook import parse_file

    for cpy, data in (("CVACT01Y", "ACCTDATA.PS"), ("CVTRA06Y", "DALYTRAN.PS")):
        layout = parse_file(str(OUT / f"{cpy}.cpy"))
        size = (OUT / data).stat().st_size
        rlen = layout.record_length()
        declared = DECLARED_LENGTHS.get(cpy)
        ok = size % rlen == 0 and (declared is None or declared == rlen)
        print(f"  {cpy} -> {data}: parsed {rlen}B, copybook comment says "
              f"{declared}B, file is {size:,}B "
              f"({size / rlen:.0f} records)  {'OK' if ok else 'MISMATCH'}")

    print("\nNote: divisibility alone does NOT identify the right copybook - every "
          "file here\ndivides evenly by several of these record lengths. Only the "
          "content discriminates.")


if __name__ == "__main__":
    main()
