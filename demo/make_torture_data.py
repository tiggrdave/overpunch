"""Data for the torture record, built so the discriminator actually decides.

The torture copybook had no data file, which meant the one decision the tool
refuses to make - which REDEFINES branch is live - could not be adjudicated
against anything. This generates records where the branch really is chosen per
record by TR-DISCRIMINATOR, so a proposed answer can be proved or refuted.

Two details are deliberate:

  * TR-ACCOUNT alone is NOT unique. Account numbers repeat across regions, so
    the obvious single-field key is wrong and the data can say so. The real key
    is TR-REGION plus TR-ACCOUNT, which is what the TR-KEY group means.
  * The two payload branches hold genuinely different kinds of bytes - text for
    a person, digits for a policy - so reading one as the other is visibly
    incoherent rather than merely different.
"""

from __future__ import annotations

import argparse
import random
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overpunch.copybook import parse_file                      # noqa: E402
from overpunch.decode import (encode_hex_float, encode_overpunch_bytes,  # noqa: E402
                              encode_packed)

SURNAMES = ["ABERNETHY", "BRANNIGAN", "CASTELLANO", "DELACROIX", "ESPOSITO",
            "FAIRWEATHER", "GHOSHAL", "HOLLOWAY", "IVERSEN", "JANKOWSKI"]
REGIONS = ["NW", "SE"]


def build(records: int, seed: int) -> tuple[bytes, dict]:
    rng = random.Random(seed)
    out = bytearray()
    per_region = records // len(REGIONS)
    truth = {"records": records, "person": 0, "policy": 0,
             "distinct_accounts": per_region,
             "distinct_region_account": records}

    for i in range(records):
        region = REGIONS[i // per_region]
        account = 4_000_000_000 + (i % per_region)      # repeats across regions

        rec = bytearray()
        rec += region.encode("cp037")                                   # 0   2
        rec += f"{account:010d}".encode("cp037")                        # 2  10

        amt = Decimal(str(round(rng.uniform(-9000, 9000), 2)))          # 12  9
        s = int(amt.scaleb(2))
        rec += encode_overpunch_bytes(str(abs(s)).rjust(9, "0"), s < 0, "cp037")

        lead = rng.randrange(-99999, 99999)                             # 21  6
        rec += (("-" if lead < 0 else "+") + f"{abs(lead):05d}").encode("cp037")
        trail = rng.randrange(-99999, 99999)                            # 27  6
        rec += (f"{abs(trail):05d}" + ("-" if trail < 0 else "+")).encode("cp037")

        rec += encode_packed(Decimal(str(round(rng.uniform(-5e4, 5e4), 2))), 11, 2)  # 33 6
        rec += encode_packed(Decimal(rng.randrange(-9999, 9999)), 4, 0)             # 39 3
        rec += rng.randrange(-9999, 9999).to_bytes(2, "big", signed=True)           # 42 2
        rec += rng.randrange(-10**8, 10**8).to_bytes(4, "big", signed=True)         # 44 4
        rec += rng.randrange(-10**12, 10**12).to_bytes(8, "big", signed=True)       # 48 8
        rec += rng.randrange(0, 9999).to_bytes(2, "big")                            # 56 2
        rec += encode_hex_float(Decimal(str(round(rng.uniform(-500, 500), 3))), 4)  # 58 4
        rec += encode_hex_float(Decimal(str(round(rng.uniform(-1e6, 1e6), 3))), 8)  # 62 8

        for _ in range(4):                                              # 70 20
            rec += encode_packed(Decimal(str(round(rng.uniform(0, 9000), 2))), 7, 2)
            rec += rng.choice("YN").encode("cp037")

        used = rng.randrange(1, 7)                                      # 90  2
        rec += encode_packed(Decimal(used), 3, 0)
        for slot in range(6):                                           # 92 48
            if slot < used:
                rec += rng.choice(["ADJ", "PRM", "FEE", "TAX"]).encode("cp037")
                rec += encode_packed(Decimal(str(round(rng.uniform(-800, 800), 2))), 9, 2)
            else:
                rec += b"\x40" * 3 + encode_packed(Decimal(0), 9, 2)

        if rng.random() < 0.55:                                         # 140 20
            truth["person"] += 1
            payload = (rng.choice(SURNAMES).ljust(14)[:14] +
                       (rng.choice("ABCDEFGHJKLM") + rng.choice("ABCDEFGHJKLM")
                        ).ljust(6))
            flag = "P"
        else:
            truth["policy"] += 1
            payload = f"{rng.randrange(10**9, 10**10):010d}" \
                      f"{rng.randrange(2026, 2036)}{rng.randrange(1,13):02d}" \
                      f"{rng.randrange(1,29):02d}" + "  "
            flag = "L"
        rec += payload.encode("cp037")
        rec += flag.encode("cp037")                                     # 160 1

        assert len(rec) == 161, len(rec)
        out += rec
    return bytes(out), truth


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--records", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261020)
    args = ap.parse_args()

    here = Path(__file__).parent
    layout = parse_file(str(here / "TORTURE.cpy"))
    data, truth = build(args.records, args.seed)
    assert len(data) == args.records * layout.record_length()
    (here / "TORTURE.dat").write_bytes(data)

    print(f"demo/TORTURE.dat  {len(data):,} bytes = {args.records} records "
          f"of {layout.record_length()}")
    print(f"  payload branch : {truth['person']} person, {truth['policy']} policy")
    print(f"  TR-ACCOUNT alone      -> {truth['distinct_accounts']} distinct "
          f"of {args.records} records  (NOT unique)")
    print(f"  TR-REGION + TR-ACCOUNT -> {truth['distinct_region_account']} distinct "
          f"of {args.records} records  (unique)")


if __name__ == "__main__":
    main()
