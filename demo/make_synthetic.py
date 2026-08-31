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
000200* UTILITY BILLING DETAIL RECORD - SYNTHETIC.
000300* Invented for demonstration. Not derived from any real system.
000400* --------------------------------------------------------------
000500 01  BILLING-DETAIL-RECORD.
000600     05  BIL-ACCOUNT-NO        PIC 9(09).
000700     05  BIL-METER-ID          PIC S9(07).
000800     05  BIL-READ-DATE.
000900         10  BIL-CC            PIC 9(02).
001000         10  BIL-YY            PIC 9(02).
001100         10  BIL-MM            PIC 9(02).
001200         10  BIL-DD            PIC 9(02).
001300     05  BIL-CHARGE-AMT        PIC S9(08)V99.
001400     05  BIL-ADJUSTMENT-AMT    PIC S9(08)V99.
001500     05  BIL-BALANCE-AMT       PIC 9(08)V99.
001600     05  BIL-RATE-CLASS        PIC X(01).
001700         88  RATE-RESIDENTIAL  VALUE 'R'.
001800         88  RATE-COMMERCIAL   VALUE 'C'.
001900         88  RATE-RELIEF       VALUE 'R'.
002000     05  BIL-CYCLE-CNT         PIC S9(04) COMP.
002100     05  BIL-PACKED-TOTAL      PIC S9(07)V99 COMP-3.
002200     05  FILLER                PIC X(10).
"""

# What is deliberately planted, so tests can require each one to be found.
PLANTED = {
    "BIL-ADJUSTMENT-AMT": "trailing overpunch sign; most adjustments are credits",
    "BIL-BALANCE-AMT": "declared PIC 9 (unsigned) but the bytes carry signs",
    "BIL-METER-ID": "declared S9(07) but only 6 significant digits ever arrive",
    "BIL-RATE-CLASS": "value 'R' claimed by two 88-levels; value 'M' claimed by none",
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
    truth = {"records": records, "adjustment_negative": 0, "balance_signed": 0,
             "rate_class_M": 0, "sum_adjustment": Decimal(0)}

    for _ in range(records):
        text = ""
        text += str(rng.randrange(10 ** 8, 10 ** 9))                       # ACCOUNT-NO  9

        # METER-ID: declared S9(07), but the meters in service only ever use 6
        meter = str(rng.randrange(100000, 1000000)).rjust(7, "0")
        text += encode_overpunch(meter, negative=False)                    # 7

        text += "20" + str(rng.randrange(17, 27)).rjust(2, "0")            # date  8
        text += str(rng.randrange(1, 13)).rjust(2, "0")
        text += str(rng.randrange(1, 29)).rjust(2, "0")

        charge = money(rng, 0, 9999)                                       # 10
        text += display_signed(charge, 10, 2)

        # ADJUSTMENT: the headline trap. Mostly credits, sign carried trailing.
        negative = rng.random() < 0.713
        adj = money(rng, 0, 4000)
        if negative:
            adj = -adj
            truth["adjustment_negative"] += 1
        truth["sum_adjustment"] += adj
        text += display_signed(adj, 10, 2)                                 # 10

        # BALANCE: copybook says unsigned, reality disagrees for the credits
        balance = money(rng, 0, 12000)
        if rng.random() < 0.07:
            truth["balance_signed"] += 1
            text += encode_overpunch(display_unsigned(balance, 10, 2), negative=True)
        else:
            text += display_unsigned(balance, 10, 2)                       # 10

        roll = rng.random()                                                # 1
        if roll < 0.55:
            text += "R"
        elif roll < 0.93:
            text += "C"
        else:
            text += "M"
            truth["rate_class_M"] += 1

        head = text.encode("cp037")
        qtr = rng.randrange(1, 5).to_bytes(2, "big", signed=True)          # 2
        packed = encode_packed(charge + adj, digits=9, scale=2)           # 5
        tail = f"CYCLE{rng.randrange(1000, 10000):04d}0".encode("cp037")[:10]  # 10
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
    (outdir / "UTLBILL.cpy").write_text(COPYBOOK)
    data, truth = build(args.records, args.seed)
    (outdir / "UTLBILL.dat").write_bytes(data)

    print(f"copybook : {outdir / 'UTLBILL.cpy'}")
    print(f"data     : {outdir / 'UTLBILL.dat'}  ({len(data):,} bytes, "
          f"{args.records:,} records of {len(data) // args.records} bytes)")
    print("planted traps:")
    for name, what in PLANTED.items():
        print(f"  {name:<22} {what}")
    print(f"ground truth: {truth['adjustment_negative']:,} negative adjustments, "
          f"true total {truth['sum_adjustment']:,.2f}")


if __name__ == "__main__":
    main()
