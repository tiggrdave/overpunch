"""Generate a synthetic mainframe extract with known traps planted in it.

Everything here is invented. No real copybook, record, or dataset from any
organisation is used or reproduced. The point of generating it is that the
traps are *known*, so the detector can be required to find them.
"""

from __future__ import annotations

import argparse
import random
from decimal import Decimal
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overpunch.decode import encode_overpunch, encode_packed  # noqa: E402

COPYBOOK = """\
000100* --------------------------------------------------------------
000200* CHARGE DETAIL RECORD - SYNTHETIC. Generated for demonstration.
000300* --------------------------------------------------------------
000400 01  CHARGE-DETAIL-RECORD.
000500     05  CHG-ACCOUNT-ID        PIC 9(09).
000600     05  CHG-EMPLOYER-NO       PIC S9(07).
000700     05  CHG-EFFECTIVE-DATE.
000800         10  CHG-CC            PIC 9(02).
000900         10  CHG-YY            PIC 9(02).
001000         10  CHG-MM            PIC 9(02).
001100         10  CHG-DD            PIC 9(02).
001200     05  CHG-BENEFIT-AMT       PIC S9(08)V99.
001300     05  CHG-ADJUSTMENT-AMT    PIC S9(08)V99.
001400     05  CHG-GROSS-AMT         PIC 9(08)V99.
001500     05  CHG-PROGRAM-CODE      PIC X(01).
001600         88  PGM-STANDARD      VALUE 'S'.
001700         88  PGM-EXTENDED      VALUE 'R'.
001800         88  PGM-REIMBURSABLE  VALUE 'R'.
001900     05  CHG-QUARTER-CNT       PIC S9(04) COMP.
002000     05  CHG-PACKED-TOTAL      PIC S9(07)V99 COMP-3.
002100     05  FILLER                PIC X(10).
"""

# What is deliberately planted, so tests can require each one to be found.
PLANTED = {
    "CHG-ADJUSTMENT-AMT": "trailing overpunch sign, ~96% negative",
    "CHG-GROSS-AMT": "declared PIC 9 (unsigned) but the bytes carry signs",
    "CHG-EMPLOYER-NO": "declared S9(07) but only 6 significant digits ever arrive",
    "CHG-PROGRAM-CODE": "value 'R' claimed by two 88-levels; value 'X' claimed by none",
    "FILLER": "declared FILLER but populated with a real value",
}


def money(rng: random.Random, lo: float, hi: float) -> Decimal:
    return Decimal(str(round(rng.uniform(lo, hi), 2)))


def display_signed(value: Decimal, digits: int, scale: int) -> str:
    scaled = int(value.scaleb(scale).to_integral_value())
    body = str(abs(scaled)).rjust(digits, "0")[-digits:]
    return encode_overpunch(body, negative=scaled < 0)


def display_unsigned(value: Decimal, digits: int, scale: int) -> str:
    scaled = int(value.scaleb(scale).to_integral_value())
    return str(abs(scaled)).rjust(digits, "0")[-digits:]


def build(records: int, seed: int) -> tuple[bytes, dict]:
    rng = random.Random(seed)
    out = bytearray()
    truth = {"records": records, "adjustment_negative": 0, "gross_signed": 0,
             "program_code_X": 0, "sum_adjustment": Decimal(0)}

    for _ in range(records):
        text = ""
        text += str(rng.randrange(10 ** 8, 10 ** 9))                       # ACCOUNT-ID  9

        # EMPLOYER-NO: declared S9(07), but the source system only ever sends 6
        emp = str(rng.randrange(100000, 1000000)).rjust(7, "0")
        text += encode_overpunch(emp, negative=False)                      # 7

        text += "20" + str(rng.randrange(17, 27)).rjust(2, "0")            # date  8
        text += str(rng.randrange(1, 13)).rjust(2, "0")
        text += str(rng.randrange(1, 29)).rjust(2, "0")

        benefit = money(rng, 0, 9999)                                      # 10
        text += display_signed(benefit, 10, 2)

        # ADJUSTMENT: the headline trap. Overwhelmingly negative, sign trailing.
        negative = rng.random() < 0.964
        adj = money(rng, 0, 4000)
        if negative:
            adj = -adj
            truth["adjustment_negative"] += 1
        truth["sum_adjustment"] += adj
        text += display_signed(adj, 10, 2)                                 # 10

        # GROSS: copybook says unsigned, reality disagrees for a minority
        gross = money(rng, 0, 12000)
        if rng.random() < 0.07:
            truth["gross_signed"] += 1
            text += encode_overpunch(display_unsigned(gross, 10, 2), negative=True)
        else:
            text += display_unsigned(gross, 10, 2)                         # 10

        roll = rng.random()                                                # 1
        if roll < 0.55:
            text += "S"
        elif roll < 0.93:
            text += "R"
        else:
            text += "X"
            truth["program_code_X"] += 1

        head = text.encode("cp037")
        qtr = rng.randrange(1, 5).to_bytes(2, "big", signed=True)          # 2
        packed = encode_packed(benefit + adj, digits=9, scale=2)           # 5
        tail = f"BATCH{rng.randrange(1000, 10000):04d}0".encode("cp037")[:10]  # 10
        out += head + qtr + packed + tail

    return bytes(out), truth


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--records", type=int, default=100_000)
    ap.add_argument("--seed", type=int, default=20261020)
    ap.add_argument("--outdir", default=str(Path(__file__).parent))
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "CHGDTL.cpy").write_text(COPYBOOK)
    data, truth = build(args.records, args.seed)
    (outdir / "CHGDTL.dat").write_bytes(data)

    print(f"copybook : {outdir / 'CHGDTL.cpy'}")
    print(f"data     : {outdir / 'CHGDTL.dat'}  ({len(data):,} bytes, "
          f"{args.records:,} records of {len(data) // args.records} bytes)")
    print("planted traps:")
    for name, what in PLANTED.items():
        print(f"  {name:<22} {what}")
    print(f"ground truth: {truth['adjustment_negative']:,} negative adjustments, "
          f"true total {truth['sum_adjustment']:,.2f}")


if __name__ == "__main__":
    main()
