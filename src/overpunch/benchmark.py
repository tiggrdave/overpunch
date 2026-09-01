"""Score models against ground truth, because this design makes that possible.

Every hypothesis a model proposes is adjudicated against the bytes, and the
deterministic pass finds its own defects with no model involved at all. Put
those together and you can measure a model rather than praise it:

  precision - of the model's testable claims about this file, what share were
              true? (CONFIRMED and INERT are both true readings of the bytes;
              REFUTED is not.)
  recall    - of the defects the deterministic pass finds on its own, how many
              did the model also spot?

Recall matters more than it looks. A model that scores 1.0 precision by
proposing three safe hypotheses has told you almost nothing; the deterministic
scan already had those. The question is what it adds.

This is the honest answer to "why this model?" - a measurement instead of an
assertion. It is only possible because nothing here trusts a model's output.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field as dc_field

from .explain import NemotronError, adjudicate, profile, propose
from .findings import evaluate
from .layout import Layout
from .probe import scan

# Probed against the live catalog on 2026-09-01. Of eighteen Nemotron and Gemma
# models the /v1/models listing advertises, five actually answer a chat request:
# the rest return a steady 404, or 410 Gone, across three attempts each. Being
# listed and being servable are different things, and a benchmark that assumed
# otherwise would report a capable model as a failure.
CANDIDATES = [
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "google/diffusiongemma-26b-a4b-it",
]


@dataclass
class Run:
    model: str
    ok: bool = True
    error: str = ""
    seconds: float = 0.0
    proposals: int = 0
    confirmed: int = 0
    inert: int = 0
    refuted: int = 0
    untestable: int = 0
    matched: list[str] = dc_field(default_factory=list)
    missed: list[str] = dc_field(default_factory=list)

    @property
    def precision(self) -> float | None:
        testable = self.confirmed + self.inert + self.refuted
        return (self.confirmed + self.inert) / testable if testable else None

    @property
    def recall(self) -> float | None:
        total = len(self.matched) + len(self.missed)
        return len(self.matched) / total if total else None


def ground_truth(layout: Layout, data_path: str, encoding: str) -> set[tuple[str, str]]:
    """What the deterministic pass finds, with no model involved.

    Informational findings are excluded: they are observations, not defects, and
    counting them would flatter a model for noticing a field is blank.
    """
    stats = scan(data_path, layout, encoding=encoding)
    return {(f.field, f.code) for f in evaluate(layout, stats)
            if f.severity in ("critical", "warn")}


def run_once(model: str, layout: Layout, copybook_text: str, data_path: str,
             encoding: str, truth: set[tuple[str, str]], limit: int | None,
             max_tokens: int) -> Run:
    r = Run(model=model)
    stats = scan(data_path, layout, encoding=encoding, limit=limit)
    started = time.time()
    try:
        hyps = propose(copybook_text, profile(layout, stats), model=model,
                       max_tokens=max_tokens)
    except (NemotronError, Exception) as exc:      # a model may simply not comply
        r.ok, r.error, r.seconds = False, f"{type(exc).__name__}: {exc}"[:160], \
            time.time() - started
        return r
    r.seconds = time.time() - started
    r.proposals = len(hyps)

    verdicts = adjudicate(hyps, layout, stats, data_path, encoding)
    for v in verdicts:
        setattr(r, v.verdict.lower(), getattr(r, v.verdict.lower()) + 1)

    found = {(v.hypothesis.field, v.hypothesis.kind)
             for v in verdicts if v.verdict == "CONFIRMED"}
    r.matched = sorted(f"{f}/{c}" for f, c in truth & found)
    r.missed = sorted(f"{f}/{c}" for f, c in truth - found)
    return r


def compare(models: list[str], layout: Layout, copybook_text: str,
            data_path: str, encoding: str = "cp037", samples: int = 3,
            limit: int | None = 20000, max_tokens: int = 12000) -> dict:
    truth = ground_truth(layout, data_path, encoding)
    results: dict[str, list[Run]] = {}
    for model in models:
        runs = []
        for _ in range(samples):
            runs.append(run_once(model, layout, copybook_text, data_path,
                                 encoding, truth, limit, max_tokens))
        results[model] = runs
    return {"truth": sorted(f"{f}/{c}" for f, c in truth),
            "samples": samples,
            "results": {m: [asdict(r) | {"precision": r.precision,
                                         "recall": r.recall}
                            for r in runs] for m, runs in results.items()}}


def table(report: dict) -> str:
    """A plain-text summary, mean across runs."""
    rows = [f"ground truth: {len(report['truth'])} defect(s) found by the "
            f"deterministic pass, with no model involved",
            "  " + "\n  ".join(report["truth"]), "",
            f"{'model':<40} {'ok':>5} {'prop':>5} {'conf':>5} {'ref':>5} "
            f"{'prec':>6} {'recall':>7} {'secs':>6}"]
    rows.append("-" * 84)
    for model, runs in report["results"].items():
        good = [r for r in runs if r["ok"]]
        if not good:
            rows.append(f"{model:<40} {'0/' + str(len(runs)):>5}   "
                        f"{runs[0]['error'][:40]}")
            continue

        def mean(key):
            vals = [r[key] for r in good if r[key] is not None]
            return sum(vals) / len(vals) if vals else None

        p, rc = mean("precision"), mean("recall")
        rows.append(
            f"{model:<40} {str(len(good)) + '/' + str(len(runs)):>5} "
            f"{mean('proposals'):>5.1f} {mean('confirmed'):>5.1f} "
            f"{mean('refuted'):>5.1f} "
            f"{(f'{p:.0%}' if p is not None else '   -'):>6} "
            f"{(f'{rc:.0%}' if rc is not None else '    -'):>7} "
            f"{mean('seconds'):>6.1f}")
    return "\n".join(rows)
