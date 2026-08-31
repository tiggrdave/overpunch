"""Deterministic COBOL copybook parser.

No model is involved here on purpose. Structure is arithmetic; only *meaning*
gets handed to a language model later, and even then its answers are proved
against the bytes before they are reported.
"""

from __future__ import annotations

import re

from .layout import Field, Layout, Picture, SignPosition, Usage

_USAGE_WORDS = {
    "COMP": Usage.COMP, "COMPUTATIONAL": Usage.COMP,
    "COMP-4": Usage.COMP, "COMPUTATIONAL-4": Usage.COMP, "BINARY": Usage.COMP,
    "COMP-3": Usage.COMP3, "COMPUTATIONAL-3": Usage.COMP3,
    "PACKED-DECIMAL": Usage.COMP3,
    "COMP-1": Usage.COMP1, "COMPUTATIONAL-1": Usage.COMP1,
    "COMP-2": Usage.COMP2, "COMPUTATIONAL-2": Usage.COMP2,
}

_TOKEN_RE = re.compile(r"""'[^']*'|"[^"]*"|[^\s]+""")


class CopybookError(ValueError):
    pass


def _strip_line(line: str) -> str:
    """Remove the sequence-number and identification areas of fixed-format source.

    Only a genuine six-digit sequence number is stripped. Free-format copybooks
    indent with spaces and put the level number in those columns, so stripping
    on indentation alone deletes the `01` and the whole parse collapses.
    """
    line = line.rstrip("\n\r")
    fixed = bool(re.match(r"^\d{6}", line))
    if fixed and len(line) > 72:
        line = line[:72]                       # identification area, cols 73-80
    if re.match(r"^(\d{6}|\s{6})[*/]", line):
        return ""                              # comment: indicator in column 7
    if line.lstrip().startswith("*"):
        return ""
    if fixed:
        line = " " * 6 + line[6:]
    return line


def _statements(text: str):
    """Yield copybook statements, joining continuation lines up to each period."""
    buf: list[str] = []
    for raw in text.splitlines():
        line = _strip_line(raw)
        if not line.strip():
            continue
        buf.append(line.strip())
        joined = " ".join(buf)
        while "." in joined:
            head, _, rest = joined.partition(".")
            if head.strip():
                yield head.strip()
            joined = rest
        buf = [joined] if joined.strip() else []
    if buf and " ".join(buf).strip():
        yield " ".join(buf).strip()


def parse_picture(pic: str) -> Picture:
    """Expand a PIC clause. `S9(7)V99` -> signed, 9 digits, scale 2."""
    src = pic.upper().replace(" ", "")
    signed = src.startswith("S")
    if signed:
        src = src[1:]

    expanded = ""
    i = 0
    while i < len(src):
        ch = src[i]
        m = re.match(r"\((\d+)\)", src[i + 1:])
        if m:
            expanded += ch * int(m.group(1))
            i += 1 + m.end()
        else:
            expanded += ch
            i += 1

    if "9" in expanded:
        before, sep, after = expanded.partition("V")
        digits = before.count("9") + after.count("9")
        return Picture(raw=pic, is_numeric=True, digits=digits,
                       scale=after.count("9") if sep else 0, signed=signed)
    chars = sum(1 for c in expanded if c in "XA9")
    return Picture(raw=pic, is_numeric=False, chars=chars)


def _tokens(stmt: str) -> list[str]:
    return _TOKEN_RE.findall(stmt)


def parse(text: str, source_name: str = "<copybook>") -> Layout:
    root: Field | None = None
    stack: list[Field] = []
    last_elementary: Field | None = None

    for stmt in _statements(text):
        tok = _tokens(stmt)
        if not tok or not tok[0].isdigit():
            continue
        level = int(tok[0])
        rest = tok[1:]

        if level == 88:
            if last_elementary is None:
                continue
            name = rest[0] if rest else "FILLER"
            vals = [t.strip("'\"") for t in rest[1:]
                    if t.upper() not in {"VALUE", "VALUES", "IS", "ARE", "THRU", "THROUGH"}]
            last_elementary.conditions.setdefault(name, []).extend(vals)
            continue
        if level == 66:
            continue

        name = "FILLER"
        idx = 0
        if rest and rest[0].upper() not in {"PIC", "PICTURE", "REDEFINES", "OCCURS"}:
            name = rest[0]
            idx = 1

        fld = Field(level=level, name=name)
        while idx < len(rest):
            word = rest[idx].upper()
            if word in {"PIC", "PICTURE"}:
                idx += 1
                if idx < len(rest) and rest[idx].upper() == "IS":
                    idx += 1
                fld.pic = parse_picture(rest[idx])
            elif word == "REDEFINES":
                idx += 1
                fld.redefines = rest[idx]
            elif word == "OCCURS":
                idx += 1
                fld.occurs = int(rest[idx])
                # OCCURS n TO m TIMES DEPENDING ON x -> reserve the maximum
                if idx + 2 < len(rest) and rest[idx + 1].upper() == "TO":
                    fld.occurs = int(rest[idx + 2])
                    idx += 2
                for j in range(idx, len(rest) - 1):
                    if rest[j].upper() == "ON":
                        fld.occurs_depending_on = rest[j + 1]
                        break
            elif word == "SIGN":
                tail = [t.upper() for t in rest[idx:]]
                if "LEADING" in tail:
                    fld.sign_position = SignPosition.LEADING
                if "SEPARATE" in tail:
                    fld.sign_separate = True
            elif word == "SEPARATE":
                fld.sign_separate = True
            elif word == "LEADING":
                fld.sign_position = SignPosition.LEADING
            elif word in _USAGE_WORDS:
                fld.usage = _USAGE_WORDS[word]
            idx += 1

        if root is None:
            if level != 1:
                raise CopybookError(f"copybook does not start at level 01: {stmt!r}")
            root = fld
            stack = [fld]
        else:
            while stack and stack[-1].level >= level:
                stack.pop()
            if not stack:
                raise CopybookError(f"orphaned level {level} field {name!r}")
            stack[-1].children.append(fld)
            stack.append(fld)

        if fld.pic is not None:
            last_elementary = fld

    if root is None:
        raise CopybookError("no 01-level record found")

    layout = Layout(root=root, source_name=source_name)
    layout.assign_offsets()
    return layout


def parse_file(path: str) -> Layout:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse(fh.read(), source_name=path)
