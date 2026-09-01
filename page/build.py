"""Build docs/index.html from the parts, with a payload of REAL tool output.

The page is not a mock-up of the tool. Every number, finding, verdict, record
and generated schema in it is produced by running overpunch here, at build time,
against the demo files in this repository. Rebuild it with:

    python page/build.py

which regenerates the payload and reassembles the page. If the tool changes its
answers, the page changes with it - which is the only way a demo stays honest.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from overpunch.copybook import parse_file                      # noqa: E402
from overpunch.decode import decode_field                       # noqa: E402
from overpunch.explain import adjudicate, parse_hypotheses      # noqa: E402
from overpunch.findings import evaluate                         # noqa: E402
from overpunch.generate import json_schema, loader_script, postgres_ddl  # noqa: E402
from overpunch.plan import build as build_plan                  # noqa: E402
from overpunch.probe import iter_records, scan                  # noqa: E402

DEMO = ROOT / "demo"
SAMPLE_RECORDS = 60
MODEL = "nvidia/nemotron-3-super-120b-a12b"

REDEFINES_ANSWER = {"discriminator": "TR-DISCRIMINATOR",
                    "map": {"P": "TR-PAYLOAD-PERSON", "L": "TR-PAYLOAD-POLICY"}}
KEY_ANSWER = {"primary_key": ["TR-ACCOUNT"]}


def pack_plan(copybook: Path, resolutions=None, policies=None) -> dict:
    layout = parse_file(str(copybook))
    # the plan records where it came from, and this page is published - keep it
    # repo-relative so a local filesystem layout never ships with it
    layout.source_name = f"demo/{copybook.name}"
    plan = build_plan(layout, encoding="cp037", policies=policies)
    if resolutions:
        for u in plan["unresolved"]:
            if u["kind"] in resolutions:
                u["resolution"] = resolutions[u["kind"]]
    rendered: dict[str, str] = {}
    try:
        rendered["ddl"] = postgres_ddl(plan)
        rendered["jsonschema"] = json_schema(plan)
        rendered["loader"] = loader_script(plan)
    except Exception as exc:                      # a plan with open decisions
        rendered["refused"] = str(exc)
    return {"copybook": copybook.name, "plan": plan, "rendered": rendered,
            "record_bytes": layout.record_length()}


SAMPLE_RECORDS_IN_UPLOADER = 60


def vision_case() -> dict:
    """Two real replies from nemotron-parse reading the same scanned page.

    One is right, one is not, and the wrong one parses as valid COBOL. Both are
    committed under tests/fixtures/, so this section shows captured output
    rather than a story about it.
    """
    import json as _json
    from overpunch.copybook import parse as parse_text
    from overpunch.vision import declared_length_in, to_copybook

    fix = ROOT / "tests" / "fixtures"
    scan_png = ROOT / "demo" / "scans" / "CVTRA06Y-scan-web.png"
    if not (fix / "parse_good.json").exists() or not scan_png.exists():
        return {}

    data_bytes = 0
    dat = DEMO / "carddemo" / "DALYTRAN.PS"
    if dat.exists():
        data_bytes = dat.stat().st_size

    reads = []
    for label, name in (("proved", "parse_good.json"),
                        ("rejected", "parse_misread.json")):
        raw = _json.loads((fix / name).read_text())
        text, kept = to_copybook(raw)
        declared = declared_length_in(" ".join(kept))
        try:
            layout = parse_text(text)
            rlen = layout.record_length()
            fields = len(layout.elementary_fields())
            err = None
        except Exception as exc:
            rlen, fields, err = 0, 0, f"{type(exc).__name__}: {exc}"
        checks = []
        if err:
            checks.append(["parses as COBOL", False, err])
        else:
            checks.append(["parses as COBOL", True,
                           f"{fields} fields, {rlen}-byte record"])
            if declared:
                checks.append(["matches the length printed on the page",
                               rlen == declared,
                               f"parsed {rlen} from the field widths, "
                               f"page says {declared}"])
            if data_bytes:
                checks.append(["divides the real data file exactly",
                               data_bytes % rlen == 0,
                               f"{data_bytes:,} / {rlen} = {data_bytes / rlen:.4f}"])
        reads.append({"label": label, "copybook": text, "checks": checks,
                      "record_len": rlen})

    return {"image": base64.b64encode(scan_png.read_bytes()).decode(),
            "model": "nvidia/nemotron-parse", "reads": reads,
            "source": "CVTRA06Y.cpy, rendered as a scan of a printout"}


def sample_case() -> dict:
    """A slice of AWS CardDemo, so the uploader has something real to chew on.

    Apache-2.0, from aws-samples/aws-mainframe-modernization-carddemo. Only a
    slice is embedded, and the fetch script downloads the rest on demand.
    """
    cpy = DEMO / "carddemo" / "CVTRA06Y.cpy"
    dat = DEMO / "carddemo" / "DALYTRAN.PS"
    if not (cpy.exists() and dat.exists()):
        return {"copybook": "", "data": "", "copybook_name": "",
                "data_name": "", "credit": "sample unavailable - "
                                           "run scripts/fetch_carddemo.py"}
    layout = parse_file(str(cpy))
    raw = dat.read_bytes()[:SAMPLE_RECORDS_IN_UPLOADER * layout.record_length()]
    return {"copybook": cpy.read_text(),
            "copybook_name": cpy.name,
            "data": base64.b64encode(raw).decode(),
            "data_name": dat.name,
            "credit": f"{SAMPLE_RECORDS_IN_UPLOADER} records of AWS CardDemo "
                      f"(Apache-2.0)"}


def german_sample() -> dict:
    """A German record in cp273, because EBCDIC is a family and Berlin is not Ohio."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "tests"))
    from test_codepages import GERMAN_COPYBOOK, german_file       # noqa: E402
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        path, _, _ = german_file(Path(td), "cp273", records=80)
        raw = Path(path).read_bytes()
    return {"copybook": GERMAN_COPYBOOK, "copybook_name": "KUNDE.cpy",
            "data": base64.b64encode(raw).decode(), "data_name": "KUNDE.dat",
            "encoding": "cp273",
            "credit": "80 German customer records in cp273"}


def build_payload() -> dict:
    cpy, dat = DEMO / "UTLBILL.cpy", DEMO / "UTLBILL.dat"
    if not dat.exists():
        raise SystemExit(f"{dat} is missing - run `python demo/make_synthetic.py` first")

    layout = parse_file(str(cpy))
    rlen = layout.record_length()
    stats = scan(str(dat), layout)

    fields = [{"name": f.name, "pic": f.pic.raw if f.pic else "", "usage": f.usage.value,
               "offset": f.offset, "len": f.total_size(),
               "signed": bool(f.pic and f.pic.signed),
               "scale": f.pic.scale if f.pic else 0,
               "numeric": bool(f.pic and f.pic.is_numeric),
               "conditions": f.conditions}
              for f in layout.elementary_fields()]

    records = []
    for i, rec in enumerate(iter_records(str(dat), rlen)):
        if i >= SAMPLE_RECORDS:
            break
        records.append(base64.b64encode(rec).decode())

    hyps = parse_hypotheses((DEMO / "nemotron-reply.json").read_text())
    verdicts = [{"field": a.hypothesis.field, "kind": a.hypothesis.kind,
                 "rationale": a.hypothesis.rationale, "meaning": a.hypothesis.meaning,
                 "verdict": a.verdict, "evidence": a.evidence}
                for a in adjudicate(hyps, layout, stats, str(dat))]

    adj = stats["BIL-ADJUSTMENT-AMT"]
    cb = (DEMO / "UTLBILL.cpy").read_text().rstrip("\n").split("\n")
    names = {f["name"] for f in fields}
    copybook_lines = []
    for n, line in enumerate(cb, start=1):
        field = None
        parts = line[6:].split()
        if len(parts) >= 2 and parts[0].isdigit():
            if parts[1] in names or parts[1] == "FILLER":
                field = parts[1]
        copybook_lines.append({"n": n, "text": line, "field": field,
                               "comment": len(line) > 6 and line[6] in "*/"})

    return {
        "record_len": rlen,
        "fields": fields,
        "records": records,
        "copybook": copybook_lines,
        "cp037": "".join(bytes([i]).decode("cp037") for i in range(256)),
        "encodings": {name: "".join(bytes([i]).decode(codec) for i in range(256))
                      for name, codec in (("cp037", "cp037"), ("cp500", "cp500"),
                                          ("cp273", "cp273"), ("cp1026", "cp1026"),
                                          ("cp1140", "cp1140"), ("latin1", "latin-1"))},
        "sample": sample_case(),
        "sample_de": german_sample(),
        "vision": vision_case(),
        "model": MODEL,
        "findings": [{"code": f.code, "severity": f.severity, "field": f.field,
                      "claim": f.claim, "evidence": f.evidence,
                      "records": f.records, "impact": f.impact}
                     for f in evaluate(layout, stats)],
        "verdicts": verdicts,
        "totals": {"records": adj.examined, "correct": str(adj.sum_correct),
                   "sign_ignored": str(adj.sum_abs), "digits_only": str(adj.sum_naive),
                   "negative": adj.overpunch_negative,
                   "positive": adj.overpunch_positive},
        "plans": {
            "utlbill": pack_plan(DEMO / "UTLBILL.cpy"),
            "torture_open": pack_plan(DEMO / "TORTURE.cpy"),
            "torture_child": pack_plan(DEMO / "TORTURE.cpy",
                                       {"REDEFINES_BRANCH": REDEFINES_ANSWER,
                                        "PRIMARY_KEY": KEY_ANSWER}),
            "torture_flat": pack_plan(DEMO / "TORTURE.cpy",
                                      {"REDEFINES_BRANCH": REDEFINES_ANSWER,
                                       "PRIMARY_KEY": KEY_ANSWER},
                                      {"occurs": "flatten"}),
        },
    }


INSPECT_SKELETON = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="description" content="Read an IBM mainframe fixed-width extract \
from its COBOL copybook, entirely in your browser. Nothing is uploaded.">
<title>Inspect a Copybook</title>
{head}
<style>
html{{-webkit-text-size-adjust:100%}}
body{{margin:0}}
img{{max-width:100%}}
[hidden]{{display:none!important}}
</style>
</head>
<body>
{body}
{script}
</body>
</html>
"""


def build_inspect(here: Path, head_css: str) -> None:
    """The tool, on its own page.

    The demo page is an argument, read top to bottom by someone who has never
    seen a copybook. This is the thing you point at a file. They want opposite
    layouts, and one page cannot be both without being worse at each - so the
    tool gets a sidebar, tabs, and none of the demo's embedded data, which is
    most of its 296 KB.
    """
    payload = {
        "encodings": {name: "".join(bytes([i]).decode(codec) for i in range(256))
                      for name, codec in (("cp037", "cp037"), ("cp500", "cp500"),
                                          ("cp273", "cp273"), ("cp1026", "cp1026"),
                                          ("cp1140", "cp1140"), ("latin1", "latin-1"))},
        "sample_de": german_sample(),
    }
    # head.part opens with the demo page's own <title>; this page has its own
    head_css = re.sub(r"<title>.*?</title>\s*", "", head_css, count=1, flags=re.S)
    head = head_css + (here / "inspect.css").read_text() + "</style>"
    libs = "".join(f"<script>\n{(here / n).read_text()}</script>\n"
                   for n in ("cobol.js", "scan.js", "ddl.js", "plan.js"))
    blob = json.dumps(payload, separators=(",", ":"))
    if "</script" in blob:
        raise SystemExit("payload would terminate its own script tag")
    script = (libs
              + f'<script id="payload" type="application/json">{blob}</script>\n'
              + f"<script>\n{(here / 'inspect.js').read_text()}</script>")
    page = INSPECT_SKELETON.format(head=head, body=(here / "inspect.html").read_text(),
                                   script=script)
    if str(ROOT) in page or "/home/" in page:
        raise SystemExit("refusing to write a page containing an absolute local path")
    out = ROOT / "docs" / "inspect.html"
    out.write_text(page)
    print(f"wrote {out.relative_to(ROOT)}  ({out.stat().st_size:,} bytes)")


SKELETON = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="description" content="A byte-level inspector for an IBM mainframe \
EBCDIC record: how a sign hidden in the last byte silently inverts a column of \
money, and how a copybook becomes a database schema without guessing.">
{head}
<style>
html{{-webkit-text-size-adjust:100%}}
body{{margin:0}}
img{{max-width:100%}}
[hidden]{{display:none!important}}
</style>
</head>
<body>
{body}
{script}
</body>
</html>
"""


def main() -> None:
    here = Path(__file__).parent
    payload = build_payload()

    head = (here / "head.part").read_text()
    body = (here / "body.part").read_text()
    script = (here / "script.part").read_text()
    css = (here / "extract.css").read_text()
    section = (here / "extract.html").read_text()
    extra_js = (here / "extract.js").read_text()

    extra_css = ((here / "analyze.css").read_text() +
                 (here / "vision.css").read_text())
    head = head.replace("@media(prefers-reduced-motion:reduce)",
                        css + extra_css + "@media(prefers-reduced-motion:reduce)")
    marker = '<section>\n  <div class="shead"><div><h2>Vocabulary</h2>'
    if marker not in body:
        raise SystemExit("vocabulary marker not found in body.part")
    body = body.replace(marker,
                        (here / "vision.html").read_text() + "\n" + section + "\n" +
                        (here / "analyze.html").read_text() + "\n" + marker)
    anchor = "renderDump();render();"
    if anchor not in script:
        raise SystemExit("javascript anchor not found in script.part")
    script = script.replace(anchor, anchor + "\n\n" + extra_js + "\n\n" +
                            (here / "analyze.js").read_text() + "\n\n" +
                            (here / "vision.js").read_text())
    # the browser parser and rules ship as their own scripts, before the app
    libs = "".join(f"<script>\n{(here / n).read_text()}</script>\n"
                   for n in ("cobol.js", "scan.js", "ddl.js"))
    script = libs + script

    blob = json.dumps(payload, separators=(",", ":"))
    if "</script" in blob:
        raise SystemExit("payload would terminate its own script tag")
    script = script.replace("__PAYLOAD__", blob)

    page = SKELETON.format(head=head, body=body, script=script)
    if str(ROOT) in page or "/home/" in page:
        raise SystemExit("refusing to write a page containing an absolute local path")
    if "\ufffd" in page:
        line = page[:page.index("\ufffd")].count("\n") + 1
        raise SystemExit(f"refusing to write a page with a literal U+FFFD at line "
                         f"{line} - write it as an escape, not as the character")
    out = ROOT / "docs" / "index.html"
    out.write_text(page)
    build_inspect(here, head[:head.rindex("</style>")])
    (ROOT / "docs" / ".nojekyll").write_text("")
    print(f"wrote {out.relative_to(ROOT)}  ({out.stat().st_size:,} bytes)")
    print(f"  {len(payload['records'])} records, {len(payload['findings'])} findings, "
          f"{len(payload['verdicts'])} verdicts, {len(payload['plans'])} plans")


if __name__ == "__main__":
    main()
