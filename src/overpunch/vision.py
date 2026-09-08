"""Read a copybook that only exists as a picture.

A great many copybooks are not files. They are printouts, or images inside a PDF
that nobody can select text out of, and the person who could retype them left in
2009. `nvidia/nemotron-parse` reads the page; everything downstream treats what
it returns as a claim, not as a fact.

That distinction is the whole design. OCR of a monospace listing is exactly the
kind of thing that fails plausibly - a `9` read as `0`, `S9(09)` as `S9(O9)` -
and the failure does not look like a failure. But a copybook makes a falsifiable
prediction: the field widths must sum to the record length, and the real data
file must divide by it exactly. So the model reads the page, and the BYTES say
whether it read it correctly.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field as dc_field

from .copybook import parse
from .layout import Layout

ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "nvidia/nemotron-parse"


class VisionError(RuntimeError):
    pass


@dataclass
class Reconstruction:
    """A copybook recovered from an image, and what could be proved about it."""
    copybook: str
    blocks: list[str] = dc_field(default_factory=list)
    layout: Layout | None = None
    parse_error: str | None = None
    checks: list[tuple[str, bool, str]] = dc_field(default_factory=list)
    attempts: int = 1

    @property
    def proved(self) -> bool:
        return bool(self.checks) and all(ok for _, ok, _ in self.checks)


def read_image(image: bytes, api_key: str, model: str = MODEL,
               timeout: int = 180) -> list[dict]:
    """Send one page to nemotron-parse and return its located text blocks."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64," +
                                  base64.b64encode(image).decode()}}]}],
        "max_tokens": 8192,
    }).encode()
    req = urllib.request.Request(ENDPOINT, data=payload, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise VisionError(f"HTTP {exc.code}: "
                          f"{exc.read()[:300].decode(errors='replace')}") from exc

    message = body["choices"][0]["message"]
    calls = message.get("tool_calls") or []
    if not calls:
        # A 200 with nothing in it. Saying so beats returning an empty copybook.
        raise VisionError("the model returned no text blocks for this image "
                          "(finish_reason="
                          f"{body['choices'][0].get('finish_reason')!r})")
    args = json.loads(calls[0]["function"]["arguments"])
    items = args[0] if args and isinstance(args[0], list) else args
    items.sort(key=lambda i: (i.get("bbox", {}).get("ymin", 0.0),
                              i.get("bbox", {}).get("xmin", 0.0)))
    return items


_TABULAR = re.compile(r"\\begin\{tabular\}.*?\n(.*)\\end\{tabular\}", re.S)
_LEVEL = re.compile(r"^\s*(\d{2})\s")


def _rows_from_tabular(text: str) -> list[str]:
    """A monospace listing often comes back as a LaTeX table, because that is
    what aligned columns look like to a document parser."""
    m = _TABULAR.search(text)
    if not m:
        return []
    out = []
    for raw in m.group(1).split("\\\\"):
        cells = [_strip_markup(c) for c in raw.replace("\\n", "\n").split("&")]
        cells = [c for c in cells if c]
        if cells:
            out.append(" ".join(cells))
    return out


_LATEX = re.compile(r"\\[a-zA-Z]+(?:\[[^\]]*\])?(?:\{([^{}]*)\})*")


def _strip_markup(text: str) -> str:
    r"""Remove the markup a document parser wraps around a cell.

    The model returns a monospace listing as a LaTeX table, and a heading inside
    it comes back as `\multicolumn{4}{c}{**...**}`. That parses harmlessly - the
    line carries no level number, so it becomes a comment - but it is markup
    leaking into something presented as a recovered copybook.
    """
    def unwrap(m):
        inner = re.findall(r"\{([^{}]*)\}", m.group(0))
        return inner[-1] if inner else ""
    text = _LATEX.sub(unwrap, text)
    text = text.replace("**", "").replace("\\", "")
    return text.strip()


def to_copybook(blocks: list[dict]) -> tuple[str, list[str]]:
    """Reassemble located text blocks into something a COBOL parser can read."""
    lines, kept = [], []
    for b in blocks:
        text = (b.get("text") or "").strip()
        if not text:
            continue
        kept.append(text[:120])
        rows = _rows_from_tabular(text)
        if rows:
            # a heading swept into the table is prose, not a statement
            lines.extend(r if _LEVEL.match(r) else "* " + r for r in rows)
            continue
        for line in text.split("\n"):
            line = _strip_markup(line)
            if not line:
                continue
            lines.append(line if _LEVEL.match(line) else "* " + line)

    # a statement needs its terminating period; OCR drops them at line ends
    fixed = []
    for line in lines:
        if _LEVEL.match(line) and not line.rstrip().endswith("."):
            line = line.rstrip() + "."
        fixed.append(line)

    # the 01 level has to come first whatever order the page was read in
    fixed.sort(key=lambda l: 0 if re.match(r"^01\s", l) else 1)
    return "\n".join(fixed) + "\n", kept


def reconstruct(image: bytes, api_key: str, data_bytes: int | None = None,
                declared_length: int | None = None, model: str = MODEL,
                attempts: int = 1) -> Reconstruction:
    """Read the page, then keep the reading only if the bytes agree with it.

    Reading a monospace listing is not reliable. Observed on one image, twice:
    a correct 350-byte layout, and a 335-byte one where DALYTRAN-ID had become
    DALYTRANS-ID and DALYTRAN-TYPE-CD had been truncated to DALY. The second
    parsed as valid COBOL with the right field count. It looked fine.

    What makes that survivable is that the check is decisive and cheap: the
    widths must sum to the length printed on the page, and the real data file
    must divide by it. So the honest way to use an unreliable reader is to
    retry it until a reading is PROVED, and to say how many attempts it took.
    """
    last = None
    for attempt in range(1, max(1, attempts) + 1):
        r = _attempt(image, api_key, data_bytes, declared_length, model)
        r.attempts = attempt
        if r.proved:
            return r
        last = r
    return last


def _attempt(image: bytes, api_key: str, data_bytes: int | None,
             declared_length: int | None, model: str) -> Reconstruction:
    blocks = read_image(image, api_key, model=model)
    text, kept = to_copybook(blocks)
    if declared_length is None:
        # the page usually states its own record length in a heading comment,
        # and the model just read it off the paper for us
        declared_length = declared_length_in(" ".join(kept))
    r = Reconstruction(copybook=text, blocks=kept)
    try:
        r.layout = parse(text, source_name="<scanned>")
    except Exception as exc:
        r.parse_error = f"{type(exc).__name__}: {exc}"
        return r

    rlen = r.layout.record_length()
    r.checks.append(("parses as COBOL", True,
                     f"{len(r.layout.elementary_fields())} fields, {rlen}-byte record"))

    # The page usually states its own record length in a comment. The parser
    # never reads comments, so agreement is two independent routes to one number.
    if declared_length is not None:
        r.checks.append(("matches the length printed on the page",
                         rlen == declared_length,
                         f"parsed {rlen} from the field widths, page says "
                         f"{declared_length}"))
    if data_bytes is not None:
        r.checks.append(("divides the real data file exactly",
                         data_bytes % rlen == 0,
                         f"{data_bytes:,} / {rlen} = {data_bytes / rlen:.4f}"))
    return r


def declared_length_in(text: str) -> int | None:
    """Pull `RECLN 350` or `RECLN = 350` out of a comment, if the page has one."""
    m = re.search(r"RECLN\s*=?\s*(\d+)", text, re.I)
    return int(m.group(1)) if m else None
