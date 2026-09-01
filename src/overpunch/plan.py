"""The extraction plan: every decision the copybook cannot make for you.

A copybook describes bytes. A database schema needs answers the bytes do not
carry - whether a repeating group becomes four columns or a child table, which
`REDEFINES` branch is live, whether trailing spaces are data, what counts as
NULL in a format that has no NULL. Generating DDL straight from a copybook means
choosing all of that silently, in code, differently in each tool.

So nothing is generated from the copybook. A PLAN is generated from the
copybook, the plan is a file a person can read, edit, review and diff, and the
DDL, the decoder and the loader are all generated from the plan. Decisions the
tool must not make alone are listed in `unresolved`, and generation refuses
while any of them is open.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field as dc_field

from .layout import Field, Layout, Usage

PLAN_VERSION = 1

# Postgres integer ceilings, by digit count
_INT_TYPES = ((4, "SMALLINT"), (9, "INTEGER"), (18, "BIGINT"))


class PlanError(ValueError):
    pass


@dataclass
class Column:
    field: str                 # the COBOL name it came from
    name: str                  # the identifier in the target
    type: str
    offset: int
    bytes: int
    nullable: bool = True
    note: str = ""


@dataclass
class Table:
    name: str
    kind: str                  # root | occurs
    columns: list[Column] = dc_field(default_factory=list)
    parent: str | None = None
    occurs_of: str | None = None
    occurs_max: int = 1
    depends_on: str | None = None


@dataclass
class Open:
    """A decision the tool refuses to make on its own."""
    id: str
    kind: str
    question: str
    options: list[str] = dc_field(default_factory=list)
    resolve_with: dict = dc_field(default_factory=dict)
    resolution: object = None

    @property
    def is_open(self) -> bool:
        return self.resolution in (None, "", {})


DEFAULT_POLICIES = {
    "identifier_style": "snake_case",
    "trim_trailing_spaces": True,
    "null_when": ["low_values"],
    # Nothing in the bytes distinguishes IBM hexadecimal float from IEEE 754.
    # Legacy extracts are overwhelmingly the former; this is a default, not a
    # measurement, and it is recorded here so it can be overridden knowingly.
    "float_encoding": "ibm_hex",
    "occurs": "child_table",
}


def normalise(name: str, style: str = "snake_case") -> str:
    ident = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    if style != "snake_case":
        ident = ident.upper()
    if ident and ident[0].isdigit():
        ident = "c_" + ident
    return ident or "unnamed"


def sql_type(fld: Field, policies: dict) -> tuple[str, str]:
    """Map one COBOL field to a Postgres type. Returns (type, note)."""
    if fld.usage is Usage.COMP1:
        return "REAL", f"float_encoding={policies['float_encoding']}"
    if fld.usage is Usage.COMP2:
        return "DOUBLE PRECISION", f"float_encoding={policies['float_encoding']}"

    pic = fld.pic
    if pic is None:
        return "BYTEA", "no PICTURE"
    if not pic.is_numeric:
        kind = "VARCHAR" if policies.get("trim_trailing_spaces") else "CHAR"
        return f"{kind}({pic.chars})", ""
    if pic.scale:
        return f"NUMERIC({pic.digits},{pic.scale})", ""
    if fld.usage is Usage.COMP:
        for ceiling, name in _INT_TYPES:
            if pic.digits <= ceiling:
                return name, ""
        return f"NUMERIC({pic.digits})", ""
    # DISPLAY / COMP-3 integers. Beyond 18 digits no integer type holds them.
    for ceiling, name in _INT_TYPES:
        if pic.digits <= ceiling:
            return name, ""
    return f"NUMERIC({pic.digits})", "exceeds BIGINT"


def _redefine_groups(layout: Layout) -> dict[str, list[Field]]:
    """Map each redefined field name to the fields competing for its bytes."""
    groups: dict[str, list[Field]] = {}
    for f in layout.walk_all():
        if f.redefines:
            groups.setdefault(f.redefines.upper(), []).append(f)
    return groups


def build(layout: Layout, table: str | None = None, encoding: str = "cp037",
          policies: dict | None = None) -> dict:
    pol = dict(DEFAULT_POLICIES)
    pol.update(policies or {})
    root_name = normalise(table or layout.root.name, pol["identifier_style"])

    redefined = _redefine_groups(layout)
    shadowed = {f.name.upper() for fs in redefined.values() for f in fs}
    for name in list(redefined):
        for f in layout.walk_all():
            if f.name.upper() == name:
                shadowed.update(c.name.upper() for c in _leaves(f))

    tables: list[Table] = [Table(name=root_name, kind="root")]
    opens: list[Open] = []
    seen: dict[str, str] = {}

    for fld in layout.elementary_fields():
        if fld.is_filler:
            continue
        if _under_redefine(fld, redefined):
            continue                      # handled as an unresolved decision
        owner = _occurs_ancestor(fld)
        if owner is not None and pol["occurs"] == "child_table":
            tbl = _child_table(tables, root_name, owner, pol)
        else:
            tbl = tables[0]
        ident = normalise(fld.name, pol["identifier_style"])
        if ident in seen and seen[ident] != fld.name:
            opens.append(Open(
                id=f"identifier:{ident}",
                kind="IDENTIFIER_COLLISION",
                question=(f"{seen[ident]!r} and {fld.name!r} both normalise to "
                          f"{ident!r}. Give one of them a different column name."),
                options=[seen[ident], fld.name],
                resolve_with={"rename": {fld.name: "<new_name>"}}))
        seen.setdefault(ident, fld.name)
        typ, note = sql_type(fld, pol)
        if owner is not None and pol["occurs"] == "flatten":
            for i in range(1, owner.occurs + 1):
                tbl.columns.append(Column(
                    field=fld.name, name=f"{ident}_{i}", type=typ,
                    offset=fld.offset + (i - 1) * owner.size(),
                    bytes=fld.total_size(), note=note))
        else:
            tbl.columns.append(Column(field=fld.name, name=ident, type=typ,
                                      offset=fld.offset, bytes=fld.total_size(),
                                      note=note))

    for target, branches in redefined.items():
        opens.append(Open(
            id=f"redefines:{target}",
            kind="REDEFINES_BRANCH",
            question=(f"{target} is redefined by "
                      f"{', '.join(b.name for b in branches)}. The same bytes "
                      f"mean different things per record and nothing in the "
                      f"copybook says which. Name the field that decides, and "
                      f"what its values mean."),
            options=[target] + [b.name for b in branches] + ["raw_bytes"],
            resolve_with={"discriminator": "<FIELD-NAME>",
                          "map": {"<value>": branches[0].name}}))

    if any(t.kind == "occurs" for t in tables):
        opens.append(Open(
            id="primary_key",
            kind="PRIMARY_KEY",
            question=("child tables need a key back to the parent row. Name the "
                      "field (or fields) that identify a record uniquely."),
            options=[c.field for c in tables[0].columns],
            resolve_with={"primary_key": ["<FIELD-NAME>"]}))

    return {
        "overpunch_plan": PLAN_VERSION,
        "source": {"copybook": layout.source_name,
                   "record_bytes": layout.record_length(),
                   "encoding": encoding,
                   "record_format": "fixed"},
        "target": {"dialect": "postgresql", "table": root_name},
        "policies": pol,
        "unresolved": [asdict(o) for o in opens],
        "tables": [asdict(t) for t in tables],
    }


def _leaves(f: Field) -> list[Field]:
    if not f.children:
        return [f]
    out: list[Field] = []
    for c in f.children:
        out.extend(_leaves(c))
    return out


def _under_redefine(fld: Field, redefined: dict) -> bool:
    node = fld
    while node is not None:
        if node.redefines:
            return True
        if node.name.upper() in redefined:
            return True
        node = node.parent
    return False


def _occurs_ancestor(fld: Field) -> Field | None:
    node = fld.parent
    while node is not None:
        if node.occurs > 1:
            return node
        node = node.parent
    return None


def _child_table(tables: list[Table], root: str, owner: Field, pol: dict) -> Table:
    name = f"{root}_{normalise(owner.name, pol['identifier_style'])}"
    for t in tables:
        if t.name == name:
            return t
    t = Table(name=name, kind="occurs", parent=root, occurs_of=owner.name,
              occurs_max=owner.occurs, depends_on=owner.occurs_depending_on)
    tables.append(t)
    return t


def open_decisions(plan: dict) -> list[dict]:
    return [u for u in plan["unresolved"] if u.get("resolution") in (None, "", {})]


def require_resolved(plan: dict) -> None:
    still = open_decisions(plan)
    if still:
        lines = [f"{len(still)} decision(s) in this plan are still open. "
                 f"Resolve them in the plan file, then generate."]
        for u in still:
            lines.append(f"\n  [{u['kind']}] {u['id']}")
            lines.append(f"    {u['question']}")
            if u.get("options"):
                lines.append(f"    options: {', '.join(u['options'])}")
            lines.append(f"    set \"resolution\" to something shaped like: "
                         f"{json.dumps(u['resolve_with'])}")
        raise PlanError("\n".join(lines))
