"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .copybook import parse_file
from .decode import decode_field
from .explain import (DEFAULT_MODEL, NemotronError, adjudicate, build_prompt,
                      parse_hypotheses, profile, propose)
from .findings import evaluate
from .generate import json_schema, loader_script, postgres_ddl
from .plan import PlanError, build as build_plan, open_decisions
from .vision import VisionError, declared_length_in, reconstruct
from .layout import Layout
from .probe import LayoutMismatch, iter_records, scan


def _layout_table(layout: Layout) -> str:
    rows = [f"{'OFF':>5}  {'LEN':>4}  {'NAME':<26} {'PIC':<14} USAGE"]
    rows.append("-" * 72)
    for f in layout.elementary_fields():
        rows.append(f"{f.offset:>5}  {f.total_size():>4}  {f.name:<26} "
                    f"{f.pic.raw:<14} {f.usage.value}")
    rows.append("-" * 72)
    rows.append(f"record length: {layout.record_length()} bytes")
    return "\n".join(rows)


def cmd_layout(args) -> int:
    layout = parse_file(args.copybook)
    print(_layout_table(layout))
    return 0


def cmd_scan(args) -> int:
    layout = parse_file(args.copybook)
    size = Path(args.data).stat().st_size
    rlen = layout.record_length()
    print(f"copybook : {args.copybook}")
    print(f"data     : {args.data}  ({size:,} bytes)")
    print(f"record   : {rlen} bytes  ->  {size / rlen:,.2f} records")
    if size % rlen:
        print()
        print(f"[CRITICAL] LAYOUT_MISMATCH")
        print(f"    the file is not a whole multiple of the copybook's record length; "
              f"{size % rlen} bytes are left over")
        print(f"    evidence: file_bytes={size:,}, record_length={rlen}, "
              f"remainder={size % rlen}")
        print("    this copybook does not describe this file. Nothing below would "
              "be trustworthy, so the scan stops here.")
        return 2

    stats = scan(args.data, layout, encoding=args.encoding, limit=args.limit)
    findings = evaluate(layout, stats)
    examined = next(iter(stats.values())).examined if stats else 0
    print(f"encoding : {args.encoding}")
    print(f"examined : {examined:,} records")
    print()
    if not findings:
        print("no findings.")
        return 0
    counts = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
        print(f)
        print()
    print("  ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    return 1 if counts.get("critical") else 0


def cmd_decode(args) -> int:
    layout = parse_file(args.copybook)
    fields = [f for f in layout.elementary_fields() if not f.is_filler]
    columns: dict[str, list] = {f.name: [] for f in fields}
    n = 0
    for rec in iter_records(args.data, layout.record_length()):
        if args.limit is not None and n >= args.limit:
            break
        for f in fields:
            raw = rec[f.offset:f.offset + f.total_size()]
            columns[f.name].append(decode_field(raw, f, args.encoding))
        n += 1

    if args.out.endswith(".parquet"):
        import pyarrow as pa
        import pyarrow.parquet as pq
        table = pa.table({k: [str(v) if hasattr(v, "as_tuple") else v for v in col]
                          for k, col in columns.items()})
        pq.write_table(table, args.out)
    else:
        import csv
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(columns.keys())
            for i in range(n):
                w.writerow([columns[k][i] for k in columns])
    print(f"wrote {n:,} records to {args.out}")
    return 0


def cmd_explain(args) -> int:
    layout = parse_file(args.copybook)
    stats = scan(args.data, layout, encoding=args.encoding, limit=args.limit)
    prof = profile(layout, stats)
    copybook_text = Path(args.copybook).read_text(encoding="utf-8", errors="replace")

    if args.save_prompt:
        Path(args.save_prompt).write_text(build_prompt(copybook_text, prof))
        print(f"prompt written to {args.save_prompt}")

    if args.hypotheses:
        hyps = parse_hypotheses(Path(args.hypotheses).read_text())
        source = f"replayed from {args.hypotheses}"
    else:
        try:
            hyps = propose(copybook_text, prof, model=args.model,
                           max_tokens=args.max_tokens,
                           samples=args.samples)
        except NemotronError as exc:
            print(f"[proposal step skipped] {exc}", file=sys.stderr)
            return 3
        source = f"proposed by {args.model}"

    if args.save_hypotheses:
        import json as _json
        Path(args.save_hypotheses).write_text(_json.dumps(
            {"_model": args.model if not args.hypotheses else "replayed",
             "_note": "captured model proposals, replayable with --hypotheses",
             "hypotheses": [h.__dict__ for h in hyps]}, indent=2) + "\n")
        print(f"proposals saved to {args.save_hypotheses}")

    verdicts = adjudicate(hyps, layout, stats, args.data, args.encoding)
    print(f"{len(hyps)} hypotheses {source}, each adjudicated against the bytes:")
    print()
    for v in verdicts:
        print(v)
        if v.hypothesis.meaning:
            print(f"     reading   : {v.hypothesis.meaning}  (advisory, not tested)")
        print()
    tally = {}
    for v in verdicts:
        tally[v.verdict] = tally.get(v.verdict, 0) + 1
    print("  ".join(f"{k.lower()}: {n}" for k, n in sorted(tally.items())))
    print()
    print("Only CONFIRMED hypotheses are findings. A REFUTED one is the model "
          "being wrong and the file saying so.")
    return 0


def cmd_plan(args) -> int:
    layout = parse_file(args.copybook)
    plan = build_plan(layout, table=args.table, encoding=args.encoding)
    text = json.dumps(plan, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text)
        print(f"plan written to {args.out}")
    else:
        print(text, end="")
    still = open_decisions(plan)
    tables = plan["tables"]
    print(f"\n{len(tables)} table(s), "
          f"{sum(len(t['columns']) for t in tables)} column(s)", file=sys.stderr)
    if still:
        print(f"{len(still)} decision(s) left open - the copybook does not "
              f"settle them and neither will this tool:", file=sys.stderr)
        for u in still:
            print(f"  [{u['kind']}] {u['question']}", file=sys.stderr)
        return 1
    return 0


def cmd_emit(args) -> int:
    plan = json.loads(Path(args.plan).read_text())
    try:
        if args.format == "ddl":
            print(postgres_ddl(plan), end="")
        elif args.format == "jsonschema":
            print(json_schema(plan))
        else:
            print(loader_script(plan), end="")
    except PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def cmd_read_scan(args) -> int:
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        print("no NVIDIA_API_KEY in the environment; a free key comes from "
              "build.nvidia.com", file=sys.stderr)
        return 3
    image = Path(args.image).read_bytes()
    data_bytes = Path(args.data).stat().st_size if args.data else None
    declared = (declared_length_in(Path(args.declares).read_text())
                if args.declares else None)

    print(f"image    : {args.image}  ({len(image):,} bytes)")
    print(f"model    : {args.model}")
    try:
        r = reconstruct(image, key, data_bytes=data_bytes,
                        declared_length=declared, model=args.model,
                        attempts=args.attempts)
    except VisionError as exc:
        print(f"[read failed] {exc}", file=sys.stderr)
        return 1

    print(f"blocks   : {len(r.blocks)} text region(s) located"
          + (f"   (attempt {r.attempts})" if r.attempts > 1 else ""))
    print()
    if args.out:
        Path(args.out).write_text(r.copybook)
        print(f"reconstructed copybook written to {args.out}")
    else:
        print(r.copybook)

    if r.parse_error:
        print(f"[CRITICAL] the reconstruction does not parse: {r.parse_error}",
              file=sys.stderr)
        return 1

    print("what could be PROVED about it, against the bytes:")
    for name, ok, detail in r.checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<44} {detail}")
    print()
    print("A model read the page. The arithmetic decided whether it read it right."
          if r.proved else
          "At least one check failed - do not trust this reconstruction.")
    return 0 if r.proved else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="overpunch",
        description="Decode mainframe fixed-width extracts from their COBOL "
                    "copybooks, and find the traps that silently corrupt them.")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("layout", help="show the record layout the copybook describes")
    p.add_argument("copybook")
    p.set_defaults(func=cmd_layout)

    p = sub.add_parser("scan", help="measure a data file against its copybook")
    p.add_argument("copybook")
    p.add_argument("data")
    p.add_argument("--encoding", default="cp037")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("decode", help="decode to Parquet or CSV")
    p.add_argument("copybook")
    p.add_argument("data")
    p.add_argument("-o", "--out", default="out.parquet")
    p.add_argument("--encoding", default="cp037")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_decode)

    p = sub.add_parser("explain",
                       help="let Nemotron propose hypotheses, then adjudicate them")
    p.add_argument("copybook")
    p.add_argument("data")
    p.add_argument("--encoding", default="cp037")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--hypotheses", help="replay a saved model reply instead of calling out")
    p.add_argument("--save-prompt", help="write the prompt that would be sent")
    p.add_argument("--samples", type=int, default=1,
                   help="ask more than once and take the union; model "
                        "recall varies between identical runs")
    p.add_argument("--save-hypotheses",
                   help="save the proposals so they can be replayed without a key")
    p.add_argument("--max-tokens", type=int, default=12000,
                   help="reasoning models need room; a truncated reply "
                        "parses as no reply at all")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("plan", help="turn a copybook into an editable extraction plan")
    p.add_argument("copybook")
    p.add_argument("-o", "--out")
    p.add_argument("--table")
    p.add_argument("--encoding", default="cp037")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("emit", help="generate DDL, JSON Schema or a loader from a plan")
    p.add_argument("plan")
    p.add_argument("--format", choices=["ddl", "jsonschema", "loader"], default="ddl")
    p.set_defaults(func=cmd_emit)

    p = sub.add_parser("read-scan",
                       help="recover a copybook from an image of a printout")
    p.add_argument("image")
    p.add_argument("--data", help="the real data file, to check the layout against")
    p.add_argument("--declares", help="a file whose text states RECLN, for cross-check")
    p.add_argument("-o", "--out")
    p.add_argument("--model", default="nvidia/nemotron-parse")
    p.add_argument("--attempts", type=int, default=1,
                   help="re-read until the layout is proved against the bytes")
    p.set_defaults(func=cmd_read_scan)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except LayoutMismatch as exc:
        print(f"[CRITICAL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
