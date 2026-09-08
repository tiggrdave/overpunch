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

from overpunch.decode import (encode_ascii_zoned, encode_hex_float,  # noqa: E402
                              encode_ieee_float, encode_overpunch_bytes,
                              encode_packed)

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
           "RECFM=VB: every record carries a 4-byte record descriptor word",
           CLEAN_CPY, bytes(out), expect=[], record_bytes=22, records=30,
           note="the 4-byte descriptor word is detected and stripped")


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


RANGE_CPY = """\
000100* A validity range beside the specific codes - ordinary COBOL.
000200 01  RANGE-REC.
000300     05  RR-ID        PIC 9(05).
000400     05  RR-CAUSE     PIC 9(01).
000500         88  RR-VALID     VALUE 1 THRU 5.
000600         88  RR-REVERSAL  VALUE 1.
000700         88  RR-OTHER     VALUE 5.
"""


def build_range():
    rng = random.Random(12)
    out = bytearray()
    for i in range(80):
        out += f"{10000 + i:05d}".encode("cp037")
        # 7 is outside the declared range and outside every listed value
        out += str(rng.choice([1, 2, 3, 4, 5, 7])).encode("cp037")
    sample("range-condition",
           "VALUE 1 THRU 5 is a range, not two values; only 7 is genuinely "
           "uncovered, and the range must not collide with VALUE 1",
           RANGE_CPY, bytes(out), expect=["UNCOVERED_VALUE"],
           record_bytes=6, records=80)


UNSET_CPY = """\
000100* Coded indicators a mainframe leaves as low-values when unset.
000200 01  UNSET-REC.
000300     05  UR-ID        PIC 9(06).
000400     05  UR-FLAG      PIC 9(01).
000500     05  UR-CLASS     PIC X(01).
000600     05  UR-AMOUNT    PIC S9(05)V99.
"""


def build_unset():
    rng = random.Random(9)
    out = bytearray()
    for i in range(60):
        out += f"{100000 + i:06d}".encode("cp037")
        out += b"\x00"                       # a numeric indicator, never set
        out += b"\x00"                       # a coded field, never set
        amount = Decimal(str(round(rng.uniform(-400, 900), 2)))
        scaled = int(amount.scaleb(2))
        out += encode_overpunch_bytes(str(abs(scaled)).rjust(7, "0"),
                                      scaled < 0, "cp037")
        assert len(out) % 15 == 0
    sample("unset-numeric",
           "low-values in a numeric field are 'not set', not a sign - the check "
           "that gets this wrong reports every empty indicator as a defect",
           UNSET_CPY, bytes(out),
           expect=["TRAILING_SIGN", "IMPLIED_DECIMAL", "NEVER_POPULATED"],
           record_bytes=15, records=60)


ASCII_ZONED_CPY = """\
000100* PC COBOL: ASCII, with the sign folded into the last byte.
000200 01  MF-REC.
000300     05  MF-REF       PIC X(08).
000400     05  MF-AMOUNT    PIC S9(07)V99.
000500     05  MF-FLAG      PIC X(01).
"""


def build_ascii_zoned():
    """Micro Focus and the other PC COBOLs, which never see EBCDIC at all.

    They write ASCII digits and set 0x40 in the zone of the last byte, so -1 is
    0x71 ('q'). It is neither the mainframe's 0xD1 nor the 'J' that survives a
    translation, and until this sample existed the tool read the whole column
    positive with the last digit dropped and said nothing.
    """
    rng = random.Random(21)
    out = bytearray()
    for i in range(60):
        amount = Decimal(str(round(rng.uniform(-900, 4000), 2)))
        scaled = int(amount.scaleb(2))
        out += f"REF{i:05d}".encode("ascii")
        out += encode_ascii_zoned(str(abs(scaled)).rjust(9, "0"), scaled < 0)
        out += rng.choice("AB").encode("ascii")
    sample("ascii-native-zoned",
           "the ASCII-native sign convention: 0x70-0x79 is a negative digit, "
           "and it is not the EBCDIC overpunch nor a translation of it",
           ASCII_ZONED_CPY, bytes(out), encoding="latin1",
           expect=["IMPLIED_DECIMAL", "TRAILING_SIGN"], record_bytes=18,
           records=60,
           note="read as EBCDIC-or-nothing this column loses every sign AND "
                "its last digit; read as cp037 it must raise UNKNOWN_SIGN_BYTE")


FLOAT_CPY = """\
000100* COMP-1 and COMP-2 carry no PICTURE and no format marker.
000200 01  FLOAT-REC.
000300     05  FR-ID        PIC 9(06).
000400     05  FR-SINGLE    COMP-1.
000500     05  FR-DOUBLE    COMP-2.
"""


def _float_records(encoder):
    rng = random.Random(31)
    out = bytearray()
    for i in range(120):
        out += f"{700000 + i:06d}".encode("cp037")
        out += encoder(Decimal(str(round(rng.uniform(0.01, 500000), 2))), 4)
        out += encoder(Decimal(str(round(rng.uniform(0.01, 900000), 2))), 8)
    return bytes(out)


def build_float_hfp():
    """The default reading, and the bytes agree with it."""
    sample("float-hex", "IBM hexadecimal float - not IEEE 754, and the "
                        "difference is invisible in any single value",
           FLOAT_CPY, _float_records(encode_hex_float),
           expect=["FLOAT_FORMAT_CONFIRMED"], record_bytes=18, records=120,
           note="measured, not assumed: 0 of 120 values are unnormalisable")


def build_float_ieee_le():
    """What GnuCOBOL on x86 actually writes - verified by compiling one.

    Same format as float-ieee, opposite byte order. The tool used to call the
    format right and the order wrong and report CONFIRMED, returning 1.16e-53
    where the value was 1.727.
    """
    sample("float-ieee-le",
           "little-endian IEEE 754, which is what a PC COBOL compiler writes; "
           "the format and the byte order are two separate unknowns",
           FLOAT_CPY,
           _float_records(lambda v, w: encode_ieee_float(v, w, little=True)),
           expect=["FLOAT_FORMAT_MISMATCH"], record_bytes=18, records=120,
           note="read as big-endian IEEE this still returns finite numbers")


def build_float_ieee():
    """The same numbers, the other format, and nothing in the bytes says so.

    Read as IBM hex float these return ordinary finite numbers - 161,916.39
    comes back as 505,354,496.00 - so only the column as a whole gives it away.
    """
    sample("float-ieee", "IEEE 754 in a COMP-1/COMP-2 field, which the default "
                         "reading turns into confident numbers off by orders "
                         "of magnitude",
           FLOAT_CPY, _float_records(encode_ieee_float),
           expect=["FLOAT_FORMAT_MISMATCH"], record_bytes=18, records=120,
           note="the discrimination pair for float-hex: same values, same "
                "copybook, one byte-level difference")


COMP5_CPY = """\
000100* A copybook that says COMP. The compiler that wrote the data
000200* said COMP-5, and nothing in the bytes records the difference.
000300 01  BIN-REC.
000400     05  BR-ID        PIC 9(06).
000500     05  BR-COUNT     PIC S9(04) COMP.
000600     05  BR-TOTAL     PIC S9(09) COMP.
"""


def _binary_records(little: bool, over_pic: bool):
    rng = random.Random(41)
    out = bytearray()
    order = "little" if little else "big"
    for i in range(120):
        out += f"{800000 + i:06d}".encode("cp037")
        # a standard COMP S9(04) truncates at 9,999; COMP-5 uses all 16 bits
        count = rng.randrange(10000, 32000) if over_pic else rng.randrange(0, 9999)
        out += count.to_bytes(2, order, signed=True)
        out += rng.randrange(0, 900000).to_bytes(4, order, signed=True)
    return bytes(out)


def build_comp5_fullrange():
    """The TRUNC hypothesis: values a standard COMP field could not hold."""
    sample("comp5-fullrange",
           "declared COMP but holding more than the PICTURE allows - the field "
           "is COMP-5, or was compiled TRUNC(BIN)",
           COMP5_CPY, _binary_records(little=False, over_pic=True),
           expect=["BINARY_EXCEEDS_PIC"], record_bytes=12, records=120,
           note="one value above 9,999 is proof; no sample size is needed")


def build_comp5_little():
    """The other half: right about the range, wrong about the byte order."""
    sample("comp5-little",
           "binary written little-endian, as a PC COBOL compiler writes COMP-5; "
           "read big-endian the values are still integers, just different ones",
           COMP5_CPY, _binary_records(little=True, over_pic=False),
           expect=["BINARY_BYTE_ORDER"], record_bytes=12, records=120)


PARTLY_UNSET_CPY = """\
000100* A coded field the source system sets only sometimes.
000200 01  PARTIAL-REC.
000300     05  PR-ID        PIC 9(05).
000400     05  PR-STATUS    PIC X(01).
000500         88  PR-OPEN      VALUE 'O'.
000600         88  PR-CLOSED    VALUE 'C'.
"""


def build_partly_unset():
    """Set in some records, low-values in the rest.

    Distinct from `never-populated`, where the field is empty in EVERY record and
    NEVER_POPULATED fires instead. Nothing in the corpus covered the in-between,
    which is why a rule present in findings.py and absent from the browser copy
    went unnoticed: the cross-check had no input that could tell them apart.
    """
    rng = random.Random(53)
    out = bytearray()
    for i in range(80):
        out += f"{30000 + i:05d}".encode("cp037")
        out += b"\x00" if i % 3 else rng.choice("OC").encode("cp037")
    sample("partly-unset",
           "a coded field set in some records and left as low-values in the rest",
           PARTLY_UNSET_CPY, bytes(out), expect=["MOSTLY_UNSET"],
           record_bytes=6, records=80,
           note="the in-between case: not NEVER_POPULATED, not fully populated")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for fn in (build_clean, build_ascii, build_packed, build_corrupt_packed,
               build_wrong_copybook, build_variable_blocked, build_blank,
               build_unset, build_range, build_ascii_zoned,
               build_float_hfp, build_float_ieee, build_float_ieee_le,
               build_comp5_fullrange, build_comp5_little,
               build_partly_unset):
        fn()
    manifest = Path(__file__).parent / "MANIFEST.json"
    manifest.write_text(json.dumps(SAMPLES, indent=2) + "\n")
    print(f"{len(SAMPLES)} samples written to {OUT.relative_to(ROOT)}/")
    for s in SAMPLES:
        exp = ", ".join(s["expect"]) or "(no findings expected)"
        print(f"  {s['name']:<22} {str(s['record_bytes']) + 'B':>6}  {exp}")


if __name__ == "__main__":
    main()
