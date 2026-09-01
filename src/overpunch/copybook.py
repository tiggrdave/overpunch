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

# Compiler-directing statements. They control the printed listing and carry no
# terminating period, so left in place they merge with the following line and
# swallow whichever field comes next.
_DIRECTIVES = {"SKIP1", "SKIP2", "SKIP3", "EJECT", "TITLE"}

# A COPY member may hold executable statements rather than a record layout.
# Saying so beats "no 01-level record found", which sounds like a parse failure.
_VERBS = {"MOVE", "IF", "ELSE", "PERFORM", "COMPUTE", "EVALUATE", "CALL", "READ",
          "WRITE", "REWRITE", "DELETE", "SET", "ADD", "SUBTRACT", "MULTIPLY",
          "DIVIDE", "STRING", "UNSTRING", "INSPECT", "SEARCH", "EXEC", "GOBACK",
          "OPEN", "CLOSE", "INITIALIZE", "ACCEPT", "DISPLAY", "PERFORM"}


class CopybookError(ValueError):
    pass


def detect_format(text: str) -> str:
    """Decide once, for the whole file, whether this is fixed or free format.

    Columns 1-6 are the sequence area and column 7 the indicator. What the
    sequence area CONTAINS is not specified: `000100` and `00001 ` are both
    ordinary, and this parser used to insist on six digits. On a real estate's
    copybooks the five-digit form was the norm, and getting it wrong is silent -
    every comment parses as a statement, `00001` becomes a level number, and the
    record comes out ZERO bytes long with no error at all.

    The signal used here is a sequence NUMBER followed by an indicator column:
    four to six leading digits, then a space, hyphen, asterisk or slash. A level
    number cannot masquerade as that, because levels are at most two digits. An
    earlier heuristic counted any line whose column 7 was followed by digits,
    which counted deeply indented 88-levels as evidence of fixed format and
    inverted the answer on free-format source.
    """
    sequenced = meaningful = 0
    for raw in text.splitlines():
        line = raw.rstrip("\n\r")
        if not line.strip():
            continue
        meaningful += 1
        if re.match(r"^\d{4,6}[\s\-*/]", line):
            sequenced += 1
    if not meaningful:
        return "free"
    return "fixed" if sequenced >= meaningful * 0.5 else "free"


def _strip_line(line: str, fmt: str = "fixed") -> str:
    """Remove the sequence-number and identification areas of fixed-format source."""
    line = line.rstrip("\n\r")
    if fmt == "fixed":
        if len(line) > 72:
            line = line[:72]               # identification area, cols 73-80
        if len(line) > 6 and line[6] in "*/":
            return ""                      # comment: indicator in column 7
        if line.lstrip().startswith("*"):
            return ""
        if len(line) <= 6:
            return ""
        line = " " * 6 + line[6:]
    elif line.lstrip().startswith("*"):
        return ""
    body = line.strip()
    if body and body.split(None, 1)[0].rstrip(".").upper() in _DIRECTIVES:
        return ""
    return line


def _terminator(text: str) -> int:
    """Index of the first period that actually ends a statement, else -1.

    In COBOL a period is a separator only when followed by a space or the end of
    the line. Splitting on every period breaks `VALUE -9999999.99.` at the
    DECIMAL POINT: the statement ends early and `99` is parsed as a level-99
    field nested under whatever came before it. Quoted literals are skipped for
    the same reason - `VALUE 'MR. SMITH'` is one literal, not two statements.
    """
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "." and (i + 1 == len(text) or text[i + 1].isspace()):
            return i
    return -1


def _statements(text: str, fmt: str | None = None):
    """Yield copybook statements, joining continuation lines up to each period."""
    fmt = fmt or detect_format(text)
    buf: list[str] = []
    for raw in text.splitlines():
        line = _strip_line(raw, fmt)
        if not line.strip():
            continue
        buf.append(line.strip())
        joined = " ".join(buf)
        while True:
            cut = _terminator(joined)
            if cut < 0:
                break
            head, joined = joined[:cut], joined[cut + 1:]
            if head.strip():
                yield head.strip()
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


def check_truncation(text: str) -> None:
    """Refuse source whose statement terminator falls past column 72.

    Columns 73-80 are the identification area and a COBOL compiler discards
    them - correctly. But if the discarded tail carried the period and the kept
    part has none, the statement never terminates: it swallows the following
    line, the field inherits a PICTURE from whatever it ate, and the record
    length comes out wrong with nothing raised.

    Detected here rather than downstream because the corrupted parse that
    results is entirely plausible. It is only wrong.
    """
    if detect_format(text) != "fixed":
        return
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\n\r")
        if len(line) <= 72:
            continue
        kept, discarded = line[6:72], line[72:]
        if "." in discarded and "." not in kept:
            raise CopybookError(
                f"line {n} runs past column 72 and its terminating period falls "
                f"in the discarded identification area, so the statement never "
                f"ends and merges with the next one. Shorten the line.\n"
                f"  kept      : {kept.rstrip()!r}\n"
                f"  discarded : {discarded!r}")


def parse(text: str, source_name: str = "<copybook>") -> Layout:
    """The first record in the copybook. See `parse_records` for all of them."""
    records = parse_records(text, source_name)
    first = records[0]
    first.other_records = [r.root.name for r in records[1:]]
    return first


def parse_records(text: str, source_name: str = "<copybook>") -> list[Layout]:
    """Every 01-level record in the copybook.

    A copybook may declare several records - 65 of 885 in the estate this was
    tested against do, one of them 24 of them. An 01 is a record boundary, not a
    continuation, and treating the second one as a field under the first raised
    'orphaned level 1' and lost everything after it.
    """
    check_truncation(text)
    fmt = detect_format(text)
    roots: list[Field] = []
    root: Field | None = None
    stack: list[Field] = []
    last_elementary: Field | None = None
    fragment = False
    verbs = pics = 0

    for stmt in _statements(text, fmt):
        tok = _tokens(stmt)
        if not tok:
            continue
        if not tok[0].isdigit():
            head = tok[0].upper().rstrip(".")
            if head in _VERBS:
                verbs += 1
            continue
        if any(t.upper() in ("PIC", "PICTURE") for t in tok):
            pics += 1
        level = int(tok[0])
        rest = tok[1:]

        if level == 88:
            if last_elementary is None:
                continue
            name = rest[0] if rest else "FILLER"
            # THRU makes a RANGE, and flattening it to its endpoints is wrong
            # twice over: it invents two discrete values that were never
            # declared, and it makes `VALUE 1 THRU 5` collide with `VALUE 1` -
            # which is idiomatic COBOL, a valid-set condition beside the
            # specific ones, not an ambiguity.
            words = [t for t in rest[1:]
                     if t.upper() not in {"VALUE", "VALUES", "IS", "ARE"}]
            vals, ranges, i = [], [], 0
            while i < len(words):
                if words[i].upper() in {"THRU", "THROUGH"}:
                    if vals and i + 1 < len(words):
                        ranges.append((vals.pop(), words[i + 1].strip("'\"")))
                        i += 2
                        continue
                    i += 1
                    continue
                vals.append(words[i].strip("'\""))
                i += 1
            if vals:
                last_elementary.conditions.setdefault(name, []).extend(vals)
            if ranges:
                last_elementary.condition_ranges.setdefault(name, []).extend(ranges)
                last_elementary.conditions.setdefault(name, [])
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

        if level == 1:
            # an 01 always begins a new record, even after a fragment
            root = fld
            roots.append(fld)
            stack = [fld]
            if fld.pic is not None:
                last_elementary = fld
            continue

        if root is None:
            # A copybook that starts below 01 is a FRAGMENT: a group of fields
            # meant to be COPY'd into a record the program declares. Every real
            # copybook in the estate this was first tested against is one of
            # these, starting at 05 or 10. Refusing them rejects the common case.
            fragment = True
            root = Field(level=0, name="<fragment>")
            roots.append(root)
            stack = [root]
            root.children.append(fld)
            stack.append(fld)
            if fld.pic is not None:
                last_elementary = fld
            continue
        else:
            while stack and stack[-1].level >= level:
                stack.pop()
            if not stack:
                raise CopybookError(f"orphaned level {level} field {name!r}")
            stack[-1].children.append(fld)
            stack.append(fld)

        if fld.pic is not None:
            last_elementary = fld

    if not roots:
        if verbs and not pics:
            raise CopybookError(
                f"this copybook holds procedure-division code, not a record "
                f"layout ({verbs} statements, no PICTURE clauses). There is "
                f"nothing here to decode a data file with.")
        raise CopybookError("no record definition found: no 01 level and no "
                            "fields to make a record from")

    out = []
    for r in roots:
        layout = Layout(root=r, source_name=source_name)
        layout.is_fragment = fragment and r.level == 0
        layout.assign_offsets()
        layout.validate()
        out.append(layout)
    return out


def parse_file(path: str) -> Layout:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse(fh.read(), source_name=path)
