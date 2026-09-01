"""Generate the sample corpus: one file per thing that can go wrong.

Each sample exists to exercise one behaviour and to have a KNOWN answer, so the
test suite can require that answer rather than agreeing with whatever the tool
happens to say. Everything is generated, deterministically, from this file -
nothing binary is committed.

    python samples/build_samples.py          # writes samples/data/
    pytest tests/test_samples.py
"""

from __future__ import annotations

import json
import random
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from overpunch.decode import (encode_overpunch_bytes, encode_packed)  # noqa: E402

OUT = Path(__file__).parent / "data"
SAMPLES: list[dict] = []


def sample(name, why, copybook, data=None, encoding="cp037", expect=(),
           record_bytes=None, records=None, note=""):
    SAMPLES.append({"name": name, "why": why, "encoding": encoding,
                    "expect": sorted(expect), "record_bytes": record_bytes,
                    "records": records, "note": note,
                    "copybook": f"{name}.cpy",
                    "data": f"{name}.dat" if data is not None else None})
    (OUT / f"{name}.cpy").write_text(copybook)
    if data is not None:
        (OUT / f"{name}.dat").write_bytes(data)


# ---------------------------------------------------------------- 1. clean
CLEAN_CPY = """\
000100* A file with nothing wrong with it.
000200 01  CLEAN-REC.
000300     05  CR-ID        PIC 9(06).
000400     05  CR-NAME      PIC X(12).
000500     05  CR-COUNT     PIC 9(04).
"""


def build_clean():
    rng = random.Random(1)
    out = bytearray()
    for i in range(50):
        out += (f"{100000+i:06d}" + f"CUSTOMER{i:04d}"[:12].ljust(12) +
                f"{rng.randrange(1000,9999):04d}").encode("cp037")
    sample("clean", "a detector that fires on every file is not a detector",
           CLEAN_CPY, bytes(out), expect=[], record_bytes=22, records=50,
           note="must produce NO findings at all")


# ------------------------------------------------- 2. ascii separate sign
ASCII_CPY = """\
000100* A modern extract: ASCII, with the sign as its own character.
000200 01  ASCII-REC.
000300     05  AR-REF       PIC X(08).
000400     05  AR-AMOUNT    PIC S9(07)V99 SIGN IS TRAILING SEPARATE.
000500     05  AR-FLAG      PIC X(01).
"""


def build_ascii():
    rng = random.Random(2)
    out = bytearray()
    for i in range(60):
        amount = Decimal(str(round(rng.uniform(-900, 4000), 2)))
        scaled = int(amount.scaleb(2))
        body = str(abs(scaled)).rjust(9, "0")
        out += (f"REF{i:05d}" + body + ("-" if scaled < 0 else "+") +
                rng.choice("AB")).encode("latin-1")
    sample("ascii-separate-sign",
           "not every fixed-width extract is EBCDIC; some carry a real +/- character",
           ASCII_CPY, bytes(out), encoding="latin1",
           expect=["IMPLIED_DECIMAL", "TRAILING_SIGN"], record_bytes=19, records=60)


# -------------------------------------------------------- 3. packed heavy
PACKED_CPY = """\
000100* Money kept the way a mainframe usually keeps it: packed.
000200 01  PACKED-REC.
000300     05  PR-ACCOUNT   PIC 9(08).
000400     05  PR-BALANCE   PIC S9(09)V99 COMP-3.
000500     05  PR-LIMIT     PIC S9(07)V99 COMP-3.
000600     05  PR-CYCLE     PIC S9(03)    COMP-3.
"""


def build_packed():
    rng = random.Random(3)
    out = bytearray()
    for i in range(40):
        out += f"{20000000+i:08d}".encode("cp037")
        out += encode_packed(Decimal(str(round(rng.uniform(-2000, 9000), 2))), 11, 2)
        out += encode_packed(Decimal(str(round(rng.uniform(0, 5000), 2))), 9, 2)
        out += encode_packed(Decimal(rng.randrange(1, 13)), 3, 0)
    sample("packed-heavy",
           "COMP-3 is two digits a byte with the sign in the final nibble; "
           "read as text it is unprintable and as a string it is truncated",
           PACKED_CPY, bytes(out), expect=["IMPLIED_DECIMAL"],
           record_bytes=8 + 6 + 5 + 2, records=40,
           note="scaled money in COMP-3 carries the same 100x trap as DISPLAY")


# ------------------------------------------------------ 4. corrupt packed
def build_corrupt_packed():
    rng = random.Random(4)
    out = bytearray()
    for i in range(40):
        out += f"{20000000+i:08d}".encode("cp037")
        if i % 4 == 0:                       # text where packed decimal belongs
            out += "ABCDEF".encode("cp037")
        else:
            out += encode_packed(Decimal(str(round(rng.uniform(0, 900), 2))), 11, 2)
        out += encode_packed(Decimal("1.00"), 9, 2)
        out += encode_packed(Decimal(1), 3, 0)
    sample("corrupt-packed",
           "a COMP-3 field that is not actually packed - wrong usage, or wrong offset",
           PACKED_CPY, bytes(out), expect=["INVALID_PACKED", "IMPLIED_DECIMAL"],
           record_bytes=21, records=40)


# ------------------------------------------------------ 5. wrong copybook
def build_wrong_copybook():
    rng = random.Random(5)
    out = bytearray()
    for _ in range(37):                      # 37 x 22 is not a multiple of 21
        out += bytes(rng.randrange(0xF0, 0xFA) for _ in range(22))
    sample("wrong-copybook",
           "the copybook does not describe this file, and nothing downstream "
           "would be trustworthy if it carried on",
           PACKED_CPY, bytes(out), expect=["LAYOUT_MISMATCH"],
           record_bytes=21, records=None,
           note="scan must refuse rather than report findings")


# --------------------------------------------------- 6. variable blocked
def build_variable_blocked():
    rng = random.Random(6)
    out = bytearray()
    for i in range(30):                      # each record prefixed with an RDW
        body = (f"{100000+i:06d}" + f"NAME{i:08d}"[:12].ljust(12) +
                f"{rng.randrange(1000,9999):04d}").encode("cp037")
        out += (len(body) + 4).to_bytes(2, "big") + b"\x00\x00" + body
    sample("variable-blocked",
           "RECFM=VB: every record carries a 4-byte record descriptor word, "
           "which this tool does not yet unpack",
           CLEAN_CPY, bytes(out), expect=["LAYOUT_MISMATCH"],
           record_bytes=22, records=None,
           note="a known limitation, and it fails loudly instead of decoding wrongly")


# ------------------------------------------------------------ 7. all-blank
BLANK_CPY = """\
000100* A field nobody ever populated.
000200 01  BLANK-REC.
000300     05  BR-ID        PIC 9(06).
000400     05  BR-RESERVED  PIC X(10).
000500     05  BR-CODE      PIC X(02).
000600         88  CODE-NEW  VALUE 'NW'.
"""


def build_blank():
    out = bytearray()
    for i in range(30):
        out += (f"{500000+i:06d}" + " " * 10 + ("NW" if i % 2 else "XX")).encode("cp037")
    sample("never-populated",
           "a reserved field carried through every migration and never used, "
           "and a code no 88-level accounts for",
           BLANK_CPY, bytes(out),
           expect=["NEVER_POPULATED", "UNCOVERED_VALUE"], record_bytes=18, records=30)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for fn in (build_clean, build_ascii, build_packed, build_corrupt_packed,
               build_wrong_copybook, build_variable_blocked, build_blank):
        fn()
    manifest = Path(__file__).parent / "MANIFEST.json"
    manifest.write_text(json.dumps(SAMPLES, indent=2) + "\n")
    print(f"{len(SAMPLES)} samples written to {OUT.relative_to(ROOT)}/")
    for s in SAMPLES:
        exp = ", ".join(s["expect"]) or "(no findings expected)"
        print(f"  {s['name']:<22} {str(s['record_bytes']) + 'B':>6}  {exp}")


if __name__ == "__main__":
    main()
