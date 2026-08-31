"""The language-model layer: Nemotron proposes, the bytes dispose.

A model is genuinely good at the part of this problem that is *reading* — a
copybook is 40-year-old English written in field names, and it knows what
`CHG-ADJ-AMT` probably means. It is not good at being believed. So it is allowed
to propose hypotheses drawn from a CLOSED vocabulary, and every one of them is
then adjudicated against the actual file by deterministic code. A hypothesis the
adjudicator cannot test is reported as an opinion and never as a finding.

The consequence worth stating plainly: a hallucination here cannot become a
finding. It can only become a REFUTED or UNTESTABLE hypothesis.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field as dc_field
from datetime import date

from .decode import decode_text, split_overpunch
from .layout import Field, Layout, Usage
from .probe import FieldStats, iter_records

NIM_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"

# The closed vocabulary. A proposal outside this set is not testable and will be
# reported as an opinion.
TESTABLE_KINDS = {
    "TRAILING_SIGN": "the final byte carries a sign rather than a digit",
    "UNDECLARED_SIGN": "an unsigned PIC 9 field whose bytes carry signs",
    "IMPLIED_DECIMAL": "the value has implied decimal places and no decimal point",
    "WIDTH_UNDERFILL": "the declared width is never fully used",
    "PACKED_NOT_PACKED": "a COMP-3 field whose nibbles are not valid packed decimal",
    "NEVER_POPULATED": "the field is blank or zero in every record",
    "POPULATED_FILLER": "a FILLER that actually carries data",
    "UNCOVERED_VALUE": "values occur that no 88-level accounts for",
    "AMBIGUOUS_CONDITION": "one value claimed by more than one condition name",
    "WRONG_ENCODING": "the chosen code page is not the one the file was written in",
    "DATE_FIELD": "the field holds calendar dates",
}


@dataclass
class Hypothesis:
    field: str
    kind: str
    rationale: str = ""
    meaning: str = ""          # advisory: what the model thinks the field is


@dataclass
class Adjudication:
    hypothesis: Hypothesis
    verdict: str               # CONFIRMED | REFUTED | UNTESTABLE
    evidence: dict = dc_field(default_factory=dict)

    def __str__(self) -> str:
        mark = {"CONFIRMED": "+", "REFUTED": "-", "UNTESTABLE": "?"}[self.verdict]
        head = f" {mark} {self.verdict:<11} {self.hypothesis.kind:<20} {self.hypothesis.field}"
        lines = [head]
        if self.hypothesis.rationale:
            lines.append(f"     model said: {self.hypothesis.rationale}")
        if self.evidence:
            lines.append("     measured  : " +
                         ", ".join(f"{k}={v}" for k, v in self.evidence.items()))
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Profiling: what the model is allowed to see. No raw records leave the machine.
# --------------------------------------------------------------------------

def profile(layout: Layout, stats: dict[str, FieldStats]) -> dict:
    """A compact, non-identifying profile of the file.

    Counts and shapes only. No field VALUES are included for numeric fields, and
    character fields contribute only their distinct-value count, so a file of
    real records can be profiled without any of it being transmitted.
    """
    fields = []
    for f in layout.elementary_fields():
        st = stats.get(f.name)
        if st is None:
            continue
        fields.append({
            "name": f.name,
            "pic": f.pic.raw,
            "usage": f.usage.value,
            "offset": f.offset,
            "bytes": f.total_size(),
            "conditions": {k: v for k, v in f.conditions.items()},
            "observed": {
                "records": st.examined,
                "blank": st.blank,
                "all_zero": st.all_zero,
                "non_digit_final_byte": st.nondigit_last_byte,
                "widest_significant_digits": st.max_significant_digits,
                "distinct_values": len(st.distinct),
                "undecodable": st.undecodable,
                "invalid_packed": st.invalid_packed,
            },
        })
    return {"record_bytes": layout.record_length(), "fields": fields}


PROMPT = """You are reading a COBOL copybook and a statistical profile of the \
fixed-width data file it describes. Propose hypotheses about problems in the \
layout that would silently corrupt the decoded values.

You may ONLY use these hypothesis kinds:
{kinds}

Return JSON only, an object with one key "hypotheses" whose value is a list of \
objects with keys: field, kind, rationale, meaning. "meaning" is your reading of \
what the field holds in plain English; keep it short. Propose at most 12. \
Do not invent field names; use only names from the copybook. Do not explain \
outside the JSON.

COPYBOOK:
{copybook}

PROFILE:
{profile}
"""


def build_prompt(copybook_text: str, prof: dict) -> str:
    kinds = "\n".join(f"  {k}: {v}" for k, v in TESTABLE_KINDS.items())
    return PROMPT.format(kinds=kinds, copybook=copybook_text,
                         profile=json.dumps(prof, indent=1))


# --------------------------------------------------------------------------
# The NIM client
# --------------------------------------------------------------------------

class NemotronError(RuntimeError):
    pass


def propose(copybook_text: str, prof: dict, model: str = DEFAULT_MODEL,
            api_key: str | None = None, timeout: int = 120) -> list[Hypothesis]:
    key = api_key or os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise NemotronError(
            "no NVIDIA_API_KEY in the environment. A free key is available from "
            "build.nvidia.com; overpunch works without one, you just lose the "
            "proposal step.")

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user",
                      "content": build_prompt(copybook_text, prof)}],
        "temperature": 0.2,
        "max_tokens": 2048,
    }).encode()

    req = urllib.request.Request(
        NIM_ENDPOINT, data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise NemotronError(f"NIM returned HTTP {exc.code}: "
                            f"{exc.read()[:300].decode(errors='replace')}") from exc
    except urllib.error.URLError as exc:
        raise NemotronError(f"could not reach {NIM_ENDPOINT}: {exc.reason}") from exc

    text = payload["choices"][0]["message"]["content"]
    return parse_hypotheses(text)


def parse_hypotheses(text: str) -> list[Hypothesis]:
    """Pull the JSON out of a model reply, tolerating fences and stray prose."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise NemotronError("model reply contained no JSON object")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise NemotronError(f"model reply was not valid JSON: {exc}") from exc
    out = []
    for item in data.get("hypotheses", []):
        if not isinstance(item, dict) or "field" not in item or "kind" not in item:
            continue
        out.append(Hypothesis(field=str(item["field"]).strip().upper(),
                              kind=str(item["kind"]).strip().upper(),
                              rationale=str(item.get("rationale", "")).strip(),
                              meaning=str(item.get("meaning", "")).strip()))
    return out


# --------------------------------------------------------------------------
# Adjudication: the part the model does not get a vote in
# --------------------------------------------------------------------------

def adjudicate(hyps: list[Hypothesis], layout: Layout,
               stats: dict[str, FieldStats], data_path: str,
               encoding: str = "cp037") -> list[Adjudication]:
    out = []
    for h in hyps:
        fld = layout.find(h.field)
        if fld is None:
            out.append(Adjudication(h, "REFUTED",
                                    {"reason": "no such field in the copybook"}))
            continue
        if h.kind not in TESTABLE_KINDS:
            out.append(Adjudication(h, "UNTESTABLE",
                                    {"reason": "outside the testable vocabulary"}))
            continue
        st = stats.get(fld.name)
        if st is None or st.examined == 0:
            # Group items are not elementary, so nothing measured them field by
            # field. Tests that read the raw bytes directly do not need that.
            if h.kind in _RAW_TESTS and fld.total_size() > 0:
                st = FieldStats(name=fld.name)
            else:
                out.append(Adjudication(h, "UNTESTABLE",
                                        {"reason": "field not measured"}))
                continue
        verdict, ev = _TESTS[h.kind](fld, st, layout, data_path, encoding)
        out.append(Adjudication(h, verdict, ev))
    return out


def _yes(cond: bool, ev: dict) -> tuple[str, dict]:
    return ("CONFIRMED" if cond else "REFUTED"), ev


def _t_trailing_sign(fld, st, layout, path, enc):
    n = st.overpunch_negative
    return _yes(bool(fld.pic.signed and n),
                {"negative_records": f"{n:,}", "of": f"{st.examined:,}",
                 "declared": fld.pic.raw})


def _t_undeclared_sign(fld, st, layout, path, enc):
    n = st.overpunch_negative + st.overpunch_positive
    return _yes(bool(not fld.pic.signed and fld.pic.is_numeric and n),
                {"signed_records": f"{n:,}", "declared": fld.pic.raw})


def _t_implied_decimal(fld, st, layout, path, enc):
    return _yes(bool(fld.pic.is_numeric and fld.pic.scale),
                {"scale": fld.pic.scale, "declared": fld.pic.raw})


def _t_width_underfill(fld, st, layout, path, enc):
    return _yes(bool(fld.pic.is_numeric and st.max_significant_digits and
                     st.max_significant_digits <= fld.pic.int_digits - 1),
                {"declared_digits": fld.pic.int_digits,
                 "widest_observed": st.max_significant_digits})


def _t_packed_not_packed(fld, st, layout, path, enc):
    return _yes(bool(st.invalid_packed), {"invalid_records": f"{st.invalid_packed:,}",
                                          "usage": fld.usage.value})


def _t_never_populated(fld, st, layout, path, enc):
    return _yes(st.examined > 0 and (st.blank == st.examined or
                                     st.all_zero == st.examined),
                {"blank": f"{st.blank:,}", "zero": f"{st.all_zero:,}",
                 "of": f"{st.examined:,}"})


def _t_populated_filler(fld, st, layout, path, enc):
    return _yes(fld.is_filler and st.blank < st.examined,
                {"non_blank": f"{st.examined - st.blank:,}", "of": f"{st.examined:,}"})


def _t_uncovered(fld, st, layout, path, enc):
    claimed = {v for vals in fld.conditions.values() for v in vals}
    seen = {v.strip() for v in st.distinct if v.strip()}
    extra = seen - claimed
    return _yes(bool(fld.conditions and extra),
                {"unclaimed_values": ", ".join(sorted(extra)[:6]) or "none",
                 "declared_conditions": len(fld.conditions)})


def _t_ambiguous(fld, st, layout, path, enc):
    c = Counter(v for vals in fld.conditions.values() for v in vals)
    dupes = [v for v, n in c.items() if n > 1]
    return _yes(bool(dupes), {"duplicated_values": ", ".join(dupes) or "none"})


_PRINTABLE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,-/()")


def _t_wrong_encoding(fld, st, layout, path, enc):
    """Re-decode this field under candidate code pages and compare legibility."""
    candidates = ["cp037", "cp500", "cp1047", "cp1140", "latin-1"]
    scores: dict[str, float] = {}
    rlen = layout.record_length()
    for cand in candidates:
        good = total = 0
        for i, rec in enumerate(iter_records(path, rlen)):
            if i >= 2000:
                break
            raw = rec[fld.offset:fld.offset + fld.total_size()]
            text = decode_text(raw, cand)
            good += sum(1 for ch in text if ch in _PRINTABLE)
            total += len(text)
        scores[cand] = round(good / total, 3) if total else 0.0
    best = max(scores, key=scores.get)
    return _yes(best != enc and scores[best] > scores.get(enc, 0) + 0.05,
                {"chosen": f"{enc}={scores.get(enc, 0)}",
                 "best": f"{best}={scores[best]}",
                 "note": "legible-character share, 2000 records"})


def _t_date_field(fld, st, layout, path, enc):
    """Does the field actually parse as calendar dates?"""
    rlen = layout.record_length()
    ok = bad = 0
    for i, rec in enumerate(iter_records(path, rlen)):
        if i >= 2000:
            break
        raw = rec[fld.offset:fld.offset + fld.total_size()]
        digits, _ = split_overpunch(decode_text(raw, enc).strip())
        digits = "".join(c for c in digits if c.isdigit())
        if len(digits) != 8:
            bad += 1
            continue
        try:
            date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
            ok += 1
        except ValueError:
            bad += 1
    share = ok / (ok + bad) if (ok + bad) else 0.0
    return _yes(share > 0.95, {"parsed_as_yyyymmdd": f"{ok:,}",
                               "failed": f"{bad:,}", "share": f"{share:.1%}"})


# Tests that work straight off the bytes, so they also apply to group items.
_RAW_TESTS = {"DATE_FIELD", "WRONG_ENCODING"}

_TESTS = {
    "TRAILING_SIGN": _t_trailing_sign,
    "UNDECLARED_SIGN": _t_undeclared_sign,
    "IMPLIED_DECIMAL": _t_implied_decimal,
    "WIDTH_UNDERFILL": _t_width_underfill,
    "PACKED_NOT_PACKED": _t_packed_not_packed,
    "NEVER_POPULATED": _t_never_populated,
    "POPULATED_FILLER": _t_populated_filler,
    "UNCOVERED_VALUE": _t_uncovered,
    "AMBIGUOUS_CONDITION": _t_ambiguous,
    "WRONG_ENCODING": _t_wrong_encoding,
    "DATE_FIELD": _t_date_field,
}
