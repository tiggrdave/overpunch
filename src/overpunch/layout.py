"""Record layout model: fields, offsets, and the width arithmetic that proves a
copybook actually describes the file it is pointed at."""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from enum import Enum


class Usage(str, Enum):
    DISPLAY = "DISPLAY"
    COMP = "COMP"          # binary, big-endian
    COMP3 = "COMP-3"       # packed decimal
    COMP1 = "COMP-1"       # single float
    COMP2 = "COMP-2"       # double float


class SignPosition(str, Enum):
    TRAILING = "TRAILING"
    LEADING = "LEADING"


@dataclass
class Picture:
    """A parsed PIC clause."""
    raw: str
    is_numeric: bool
    digits: int = 0            # total digits, both sides of the implied decimal
    scale: int = 0             # digits to the right of the implied V
    signed: bool = False
    chars: int = 0             # for PIC X, the character count

    @property
    def int_digits(self) -> int:
        return self.digits - self.scale


@dataclass
class Field:
    level: int
    name: str
    pic: Picture | None = None
    usage: Usage = Usage.DISPLAY
    sign_separate: bool = False
    sign_position: SignPosition = SignPosition.TRAILING
    occurs: int = 1
    occurs_depending_on: str | None = None
    redefines: str | None = None
    conditions: dict[str, list[str]] = dc_field(default_factory=dict)  # 88-levels
    children: list["Field"] = dc_field(default_factory=list)
    offset: int = 0            # byte offset from start of record, filled by Layout
    parent: "Field | None" = None

    @property
    def is_group(self) -> bool:
        return not self.children == []

    @property
    def is_filler(self) -> bool:
        return self.name.upper() == "FILLER"

    def size(self) -> int:
        """Storage bytes for ONE occurrence of this field."""
        if self.children:
            total = 0
            for c in self.children:
                if c.redefines:          # shares bytes with a sibling, adds nothing
                    continue
                total += c.size() * c.occurs
            return total
        return self._elementary_size()

    def _elementary_size(self) -> int:
        # COMP-1/COMP-2 are declared with a USAGE and no PICTURE at all
        if self.usage is Usage.COMP1:
            return 4
        if self.usage is Usage.COMP2:
            return 8
        p = self.pic
        if p is None:
            return 0
        if not p.is_numeric:
            return p.chars
        if self.usage is Usage.COMP3:
            # packed: two digits per byte plus a sign nibble, rounded up
            return p.digits // 2 + 1
        if self.usage is Usage.COMP:
            if p.digits <= 4:
                return 2
            if p.digits <= 9:
                return 4
            return 8
        # DISPLAY numeric: one byte per digit, plus a byte if the sign is separate
        return p.digits + (1 if (p.signed and self.sign_separate) else 0)

    def total_size(self) -> int:
        return self.size() * self.occurs


class LayoutError(ValueError):
    pass


@dataclass
class Layout:
    """A whole record layout: the 01-level and everything under it."""
    root: Field
    source_name: str = "<copybook>"

    def record_length(self) -> int:
        return self.root.size()

    def assign_offsets(self) -> None:
        self._walk_offsets(self.root, 0)

    def _walk_offsets(self, f: Field, base: int) -> int:
        f.offset = base
        if not f.children:
            return base + f.total_size()
        cursor = base
        for c in f.children:
            c.parent = f
            if c.redefines:
                # REDEFINES starts wherever the field it redefines started
                target = self.find(c.redefines)
                self._walk_offsets(c, target.offset if target else cursor)
                continue
            end = self._walk_offsets(c, cursor)
            # offsets are laid out for the FIRST occurrence, but the cursor has to
            # step over all of them or every later field lands too early
            cursor = cursor + (end - cursor) * c.occurs
        return cursor

    def validate(self) -> None:
        """Refuse a layout whose offsets and record length disagree.

        Both are derived from the same field widths by different routes, so they
        can only differ if the walk is wrong. Returning such a layout means every
        field after the fault reads the wrong bytes and nothing says so.
        """
        for f in self.walk_all():
            if f.children and f.pic is not None:
                raise LayoutError(
                    f"{f.name!r} has both a PICTURE and subordinate fields, which "
                    f"COBOL does not allow. The usual cause is a statement that "
                    f"lost its terminating period - check for a line running past "
                    f"column 72.")
        leaves = self.elementary_fields()
        if not leaves:
            return
        reach = max(f.offset + f.total_size() for f in leaves)
        if reach > self.record_length():
            raise LayoutError(
                f"field offsets reach byte {reach} but the record is "
                f"{self.record_length()} bytes; the layout disagrees with itself")

    def walk_all(self) -> list[Field]:
        out: list[Field] = []

        def rec(f: Field) -> None:
            out.append(f)
            for c in f.children:
                rec(c)

        rec(self.root)
        return out

    def elementary_fields(self) -> list[Field]:
        out: list[Field] = []

        def rec(f: Field) -> None:
            if f.children:
                for c in f.children:
                    rec(c)
            elif f.pic is not None or f.usage in (Usage.COMP1, Usage.COMP2):
                out.append(f)

        rec(self.root)
        return out

    def find(self, name: str) -> Field | None:
        want = name.upper()

        def rec(f: Field) -> Field | None:
            if f.name.upper() == want:
                return f
            for c in f.children:
                hit = rec(c)
                if hit:
                    return hit
            return None

        return rec(self.root)
