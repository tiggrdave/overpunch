"""Propose the answers a copybook cannot give, and prove them against the file.

`overpunch plan` stops on the decisions the bytes do not carry: which REDEFINES
branch is live, and what identifies a record. Both answers are *readable* in the
copybook by a person - `88 PAYLOAD-IS-PERSON VALUE 'P'` is not subtle - and
neither is derivable from it, which is exactly the gap a language model fills.

The tool still does not decide. It does the legwork and then tries hard to prove
itself wrong:

  * a proposed key must actually be UNIQUE across every record in the file;
  * a proposed discriminator must make its branch fit the bytes BETTER than the
    branch it competes with, on the records it claims, and worse on the others.

Both are two-sided. A plausible guess fails them.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field as dc_field

from .decode import decode_text
from .explain import NIM_ENDPOINT, NemotronError, _json_candidates
from .layout import Field, Layout, Usage
from .probe import iter_records

DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
_PRINTABLE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                 "0123456789 .,-/()&'")


@dataclass
class Proposal:
    decision_id: str
    kind: str
    resolution: dict
    why: str = ""


@dataclass
class Verdict:
    proposal: Proposal
    verdict: str                      # CONFIRMED | REFUTED | UNTESTABLE
    evidence: dict = dc_field(default_factory=dict)

    def __str__(self) -> str:
        mark = {"CONFIRMED": "+", "REFUTED": "-", "UNTESTABLE": "?"}[self.verdict]
        lines = [f" {mark} {self.verdict:<11} {self.proposal.kind:<20} "
                 f"{self.proposal.decision_id}",
                 f"     proposed  : {json.dumps(self.proposal.resolution)}"]
        if self.proposal.why:
            lines.append(f"     because   : {self.proposal.why}")
        if self.evidence:
            lines.append("     measured  : " +
                         ", ".join(f"{k}={v}" for k, v in self.evidence.items()))
        return "\n".join(lines)


PROMPT = """A COBOL copybook cannot answer these questions on its own, but a \
person reading it can. Answer them.

Return JSON only: an object with key "resolutions", a list of objects with keys \
"id", "resolution" and "why". Use the exact shape shown for each question. Use \
only field names that appear in the copybook. No prose outside the JSON.

QUESTIONS:
{questions}

COPYBOOK:
{copybook}
"""


def build_prompt(plan: dict, copybook_text: str) -> str:
    questions = []
    for u in plan["unresolved"]:
        if u.get("resolution"):
            continue
        questions.append(f"- id: {u['id']}\n  kind: {u['kind']}\n"
                         f"  question: {u['question']}\n"
                         f"  answer shape: {json.dumps(u['resolve_with'])}")
    return PROMPT.format(questions="\n".join(questions), copybook=copybook_text)


def propose(plan: dict, copybook_text: str, api_key: str | None = None,
            model: str = DEFAULT_MODEL, timeout: int = 300,
            max_tokens: int = 8000) -> list[Proposal]:
    key = api_key or os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise NemotronError("no NVIDIA_API_KEY in the environment")

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user",
                      "content": build_prompt(plan, copybook_text)}],
        "temperature": 0.0, "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(NIM_ENDPOINT, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "Accept": "application/json"})
    # Transient failures on a shared endpoint are normal; failing a whole run
    # on one of them is not.
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.load(resp)
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200].decode(errors="replace")
            last = NemotronError(f"HTTP {exc.code}: {detail}")
            # 404 is in this list because the endpoint really does return one,
            # with an empty body, while it is unwell - observed twice, either
            # side of a 503 saying "temporarily overloaded", on a request that
            # succeeded unchanged on the next attempt
            if exc.code not in (404, 429, 500, 502, 503, 504):
                raise last from exc
            time.sleep(2 ** attempt)
        except urllib.error.URLError as exc:
            last = NemotronError(f"could not reach {NIM_ENDPOINT}: {exc.reason}")
            time.sleep(2 ** attempt)
    else:
        raise last
    return parse_proposals(payload["choices"][0]["message"].get("content") or "",
                           plan)


def parse_proposals(text: str, plan: dict) -> list[Proposal]:
    kinds = {u["id"]: u["kind"] for u in plan["unresolved"]}
    data = None
    for candidate in _json_candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "resolutions" in parsed:
            data = parsed
            break
    if data is None:
        raise NemotronError("model reply contained no parseable 'resolutions' "
                            "object (a truncated reply looks like this)")
    out = []
    for item in data["resolutions"]:
        if not isinstance(item, dict) or "id" not in item:
            continue
        out.append(Proposal(decision_id=str(item["id"]),
                            kind=kinds.get(str(item["id"]), "UNKNOWN"),
                            resolution=item.get("resolution") or {},
                            why=str(item.get("why", "")).strip()))
    return out


# --------------------------------------------------------------------------
# adjudication - the part the model does not get a vote in
# --------------------------------------------------------------------------

def _looks_right(fld: Field, raw: bytes, encoding: str) -> bool:
    """Do these bytes look like what this field says it holds?"""
    if fld.pic is None:
        return True
    text = decode_text(raw, encoding)
    if fld.pic.is_numeric and fld.usage is Usage.DISPLAY:
        body = text[:-1] if fld.pic.signed else text
        return all(c.isdigit() for c in body) and bool(body)
    if not fld.pic.is_numeric:
        return all(c in _PRINTABLE for c in text)
    return True


def _leaves(fld: Field) -> list[Field]:
    if not fld.children:
        return [fld] if fld.pic is not None else []
    out: list[Field] = []
    for c in fld.children:
        out.extend(_leaves(c))
    return out


def _branch_fit(layout: Layout, data_path: str, encoding: str,
                discriminator: Field, mapping: dict[str, str],
                branches: list[str]) -> dict:
    """For each discriminator value, how well does each branch fit the bytes?"""
    fit = {v: {b: [0, 0] for b in branches} for v in mapping}
    seen = {v: 0 for v in mapping}
    fields = {b: _leaves(layout.find(b)) for b in branches if layout.find(b)}

    for rec in iter_records(data_path, layout.record_length()):
        d = decode_text(rec[discriminator.offset:
                            discriminator.offset + discriminator.total_size()],
                        encoding).strip()
        if d not in fit:
            continue
        seen[d] += 1
        for b, leaves in fields.items():
            for f in leaves:
                raw = rec[f.offset:f.offset + f.total_size()]
                fit[d][b][1] += 1
                if _looks_right(f, raw, encoding):
                    fit[d][b][0] += 1
    return {"fit": fit, "seen": seen}


def adjudicate(proposals: list[Proposal], plan: dict, layout: Layout,
               data_path: str, encoding: str = "cp037") -> list[Verdict]:
    out = []
    for p in proposals:
        if p.kind == "PRIMARY_KEY":
            out.append(_test_key(p, layout, data_path, encoding))
        elif p.kind == "REDEFINES_BRANCH":
            out.append(_test_branch(p, plan, layout, data_path, encoding))
        else:
            out.append(Verdict(p, "UNTESTABLE",
                               {"reason": f"no test for {p.kind}"}))
    return out


def _test_key(p: Proposal, layout: Layout, data_path: str,
              encoding: str) -> Verdict:
    names = p.resolution.get("primary_key") or []
    fields = [layout.find(n) for n in names]
    missing = [n for n, f in zip(names, fields) if f is None]
    if not names or missing:
        return Verdict(p, "REFUTED",
                       {"reason": f"no such field: {', '.join(missing) or 'none named'}"})

    seen: set[tuple] = set()
    total = blank = 0
    for rec in iter_records(data_path, layout.record_length()):
        total += 1
        key = tuple(decode_text(rec[f.offset:f.offset + f.total_size()], encoding)
                    for f in fields)
        if not "".join(key).strip():
            blank += 1
        seen.add(key)

    unique = len(seen) == total and blank == 0
    return Verdict(p, "CONFIRMED" if unique else "REFUTED", {
        "key": " + ".join(names),
        "distinct": f"{len(seen):,}",
        "records": f"{total:,}",
        "duplicates": f"{total - len(seen):,}",
        "blank": f"{blank:,}",
    })


def _test_branch(p: Proposal, plan: dict, layout: Layout, data_path: str,
                 encoding: str, margin: float = 0.05) -> Verdict:
    """Does each branch fit the records it CLAIMS better than the ones it does not?

    The obvious test - compare the branches against each other on one value -
    does not discriminate. A branch of PIC X fields accepts anything printable,
    including a policy number, so it scores 100% on every record and the correct
    mapping gets rejected for tying with it. That is what the first version of
    this function did.

    The signal is the asymmetry across values, not the comparison within one. A
    branch that is genuinely selected fits its own records better than the
    others; a branch that is genuinely NOT selected fits them worse. So:

      * no branch may fit its own records WORSE than the rest - that is a
        contradiction, and it is what catches a mapping applied backwards;
      * at least one branch must fit its own records BETTER - otherwise the
        proposed discriminator distinguishes nothing and cannot be confirmed.

    A branch that ties either way is neither evidence nor contradiction. `PIC X`
    fields tie constantly and that is fine; the numeric branch does the work.
    """
    disc_name = p.resolution.get("discriminator")
    mapping = p.resolution.get("map") or {}
    disc = layout.find(disc_name) if disc_name else None
    if disc is None:
        return Verdict(p, "REFUTED", {"reason": f"no such field: {disc_name!r}"})
    if not mapping:
        return Verdict(p, "REFUTED", {"reason": "no value-to-branch mapping given"})

    target = p.decision_id.split(":", 1)[-1]
    branches = [f.name for f in layout.walk_all()
                if f.redefines and f.redefines.upper() == target.upper()]
    unknown = [b for b in mapping.values() if b not in branches]
    if unknown:
        return Verdict(p, "REFUTED",
                       {"reason": f"not a branch of {target}: {', '.join(unknown)}",
                        "branches": ", ".join(branches)})

    measured = _branch_fit(layout, data_path, encoding, disc, mapping, branches)
    fit, seen = measured["fit"], measured["seen"]

    def share(value: str, branch: str) -> float:
        ok, total = fit[value][branch]
        return ok / total if total else 0.0

    detail, contradictions, evidence = [], [], []
    for value, chosen in mapping.items():
        if seen.get(value, 0) == 0:
            contradictions.append(f"{value!r} never occurs")
            continue
        mine = share(value, chosen)
        others = [share(v, chosen) for v in mapping if v != value and seen.get(v)]
        theirs = sum(others) / len(others) if others else None
        if theirs is None:
            detail.append(f"{chosen} on {value!r}={mine:.0%} (nothing to compare)")
            continue
        gap = mine - theirs
        detail.append(f"{chosen}: own {mine:.0%} vs other {theirs:.0%}")
        if gap < -margin:
            contradictions.append(f"{chosen} fits {value!r} WORSE than the rest")
        elif gap > margin:
            evidence.append(chosen)

    if contradictions:
        verdict, why = "REFUTED", "; ".join(contradictions)
    elif not evidence:
        verdict, why = "REFUTED", ("no branch fits its own records better than the "
                                   "others - this field does not decide anything")
    else:
        verdict, why = "CONFIRMED", f"discriminated by {', '.join(evidence)}"

    return Verdict(p, verdict, {
        "discriminator": disc_name,
        "verdict_because": why,
        "fit": " | ".join(detail),
        "note": "share of a branch's fields whose bytes match what they declare",
    })
