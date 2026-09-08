# Changelog

## 0.2.0 — 2026-09-08

Everything here began with a reader's comment on the post announcing 0.1.0: the
zoned-decimal sign overpunch differs between EBCDIC and ASCII. He was right, and
the gap was ours. Chasing it, and then an outside review of the code, turned up
four more defects — every one of them the same shape the tool exists to report: a
plausible wrong number, returned with no complaint.

### The bugs, in the order they were found

- **The ASCII-native zoned sign was not read.** The decoder knew the mainframe's
  zone nibble and the `}JKLMNOPQR` a translated file leaves behind, but not the
  convention Micro Focus and the other PC COBOLs write, where the sign is `0x40`
  set in the zone. `-123.45` read as `12.34` — sign lost *and* the last digit
  dropped, silently.
- **`decode_packed` turned junk into a number.** `str()` on a nibble above 9
  emitted `"10"`–`"15"` into the digit string, so three arbitrary bytes returned
  **1,515,151,515**.
- **`decode` never called `scan`.** The one command producing something a
  database would load was the one command running none of the checks: a file
  `scan` calls `INVALID_PACKED` over 25% of its records decoded to
  `121122123124125.12` in the extract.
- **`COMP-1`/`COMP-2` were assumed, not measured.** The docstring conceded
  nothing in the bytes distinguishes IBM hexadecimal float from IEEE 754, and the
  tool picked one anyway.
- **`COMP-5`/`COMP-X` were not recognised at all** and fell through to
  `DISPLAY`, sizing `PIC S9(04) COMP-5` as 4 bytes where the truth is 2 — which
  shifts every field after it and changes the record length. 0.1.0's README
  called this "would be read byte-reversed"; it was worse than that.
- **Byte order was assumed too** — found only by compiling a COBOL program.
  GnuCOBOL on x86 writes floats in native order, so the tool named the format
  correctly, read the bytes backwards, and reported `FLOAT_FORMAT_CONFIRMED` over
  `1.16e-53` where the value was `1.727`.

### Added

- ASCII-native zoned decoding, selected by code page, never guessed.
- `UNKNOWN_SIGN_BYTE` — a final byte that is not a digit, not a sign convention
  this decoder implements, and not padding is now a finding. The rarer ASCII
  conventions fail **closed** rather than decoding wrong.
- `FLOAT_FORMAT_MISMATCH` / `_CONFIRMED` / `_UNDECIDABLE`, and
  `--float-format hfp | ieee | ieee-le`. Both the format and the byte order are
  measured; a column that cannot settle either says so instead of confirming a
  default.
- `COMP-5`/`COMP-X` as a real usage, with `BINARY_EXCEEDS_PIC` (a value above
  what the PICTURE allows is proof the field is not standard `COMP`) and
  `BINARY_BYTE_ORDER`, plus `--binary-byteorder big | little`. The byte-order
  test is evaluated first, because the wrong order makes everything look
  over-sized for the wrong reason.
- `--force` on `decode`, which writes past critical findings but leaves
  unreadable values **empty and counted**, never guessed. A layout mismatch is
  not forceable.
- `scripts/validate_with_gnucobol.py` — compiles COBOL with GnuCOBOL and holds
  every decoder to the bytes that come out. 10 checks; skipped when `cobc` is
  absent. This is the only external validation in the project, and it is what
  found the byte-order defect.
- `scripts/regress.py` — full regression from a cold anonymous clone. Three
  defects have been visible only from here.

### Fixed

- `make test` reported **`1 failed`** on every cold clone since the vision tests
  landed: a test read a CardDemo fixture the Makefile does not fetch, with no
  skip guard. Anyone who cloned this repository ran the README's own command and
  saw a failure.
- `make verify-js` printed **`0 disagreements` while comparing 11 of 12 files** —
  a generated demo file was missing and `reference.py` skipped it in silence. A
  missing case is now a failure, and the target builds what it needs.
- A browser/Python divergence the new corpus cases exposed: JS `digitsOf()`
  defaulted an empty digit string to `"0"`, so the page reported
  `NEVER_POPULATED` on a column populated in all 60 records.
- README test counts, which were transcribed from a working tree that has
  fixtures a clone does not.

### Verified

382 passed / 11 skipped from a cold clone with the real-world fixtures fetched
(358 without them); **0 disagreements** between the Python and browser
implementations across 17 files; 10/10 against GnuCOBOL; and the headline 47%
CardDemo overstatement recomputed from the tool's own output.

### Known, and stated rather than hidden

- A binary column that uses its full range at both ends gives no byte-order
  signal; `BINARY_BYTE_ORDER` stays silent rather than guessing.
- A float column whose values all sit inside one binade cannot be told apart, in
  either format. The tool reports `FLOAT_FORMAT_UNDECIDABLE`.

## 0.1.0 — 2026-09-06

First public release: copybook parser, byte-level decoders, the finding rules,
the Nemotron proposer with arithmetic adjudication, and the browser page.
