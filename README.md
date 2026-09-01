# overpunch

Decode IBM mainframe fixed-width extracts from their COBOL copybooks — and find
the traps that silently corrupt the numbers on the way out.

Plenty of libraries will read a copybook. This one assumes the copybook is
**wrong about the file**, and goes looking for the places where it is.

```
[CRITICAL] TRAILING_SIGN        BIL-ADJUSTMENT-AMT
    the last byte carries the sign, not a digit; dropping it inverts the negative records
    evidence: negative=71,294, positive=28,706, share_negative=71.3%
    impact: correct total -85,576,733.06; sign ignored 200,006,656.22
            (overstated by 285,583,389.28)
```

One column. One byte per record. A $285 million swing, and nothing anywhere
raises an error — the load succeeds, the dashboard renders, the sign is just
gone.

## Why this keeps happening

A mainframe extract is bytes, not a file format. Everything that gives those
bytes meaning lives in a copybook written decades ago, and several of the
conventions it uses have no representation in the data at all:

| Convention | What goes wrong |
|---|---|
| **Trailing overpunch sign** | `PIC S9(8)V99` puts the sign in the *zone nibble of the last byte*. `000006000}` is −600.00. Read the digits only and you get 6000 — wrong sign, wrong magnitude. |
| **Implied decimal** | `V99` is not stored. There is no decimal point in the file. A digits-only read is 100× too large. |
| **Packed decimal** | `COMP-3` holds two digits per byte with a sign nibble. As text it is garbage; as a string it is silently truncated. |
| **`REDEFINES`** | The same bytes carry two different meanings, and which one applies is decided by a field somewhere else. |
| **Duplicate `88`-levels** | Two condition names claiming the same value. Genuinely ambiguous — no tool can resolve it, and one that guesses is worse than one that stops. |
| **`FILLER` that isn't** | Reserved space that a later change quietly started using. |
| **Code page** | cp037 vs cp500 vs cp1047 differ on characters people actually use. Mis-decoding is silent. |

None of these throw. That is the whole problem: **the failure mode of a
mainframe extract is a plausible wrong number**, not a crash.

## What it does

```bash
overpunch layout  UTLBILL.cpy                 # what the copybook says the record is
overpunch scan    UTLBILL.cpy UTLBILL.dat      # measure the file against it
overpunch decode  UTLBILL.cpy UTLBILL.dat -o out.parquet
overpunch explain UTLBILL.cpy UTLBILL.dat      # Nemotron proposes, the bytes dispose
```

`scan` reports nothing it has not measured. Every finding carries the number of
records it was observed in, and where the consequence is arithmetic it shows the
arithmetic — the correct total beside the wrong one. `scan` refuses to report
anything at all if the file is not a whole multiple of the copybook's record
length, because in that case the copybook does not describe the file and nothing
downstream of that would be trustworthy.

## The model layer, and why it cannot lie to you

A copybook is forty-year-old English hiding in field names, and a language model
is genuinely good at reading it. It is not good at being believed. So the design
splits the two jobs:

```
Nemotron (via NVIDIA NIM)  ─proposes─▶  hypotheses from a CLOSED vocabulary
                                              │
deterministic prober       ─tests────▶  CONFIRMED · REFUTED · UNTESTABLE
                                              │
                                        only CONFIRMED becomes a finding
```

The model may only propose from eleven hypothesis kinds the prober knows how to
test against the bytes. Anything else comes back `UNTESTABLE` and is printed as
an opinion, clearly labelled, never as a finding. A field name it invents comes
back `REFUTED — no such field in the copybook`.

A fourth verdict, `INERT`, exists because "wrong" and "right but harmless here"
are different things. When the model says a field carries its sign in the last
byte and it *does*, but no record in the file is negative, calling that REFUTED
would tell you it misread the bytes. It didn't.

**A hallucination cannot become a finding here.** It can only become a refuted
hypothesis. From a real run against `nvidia/nemotron-3-super-120b-a12b`, saved
to `demo/nemotron-reply.json` so `make demo` can replay it with no key and no
network call:

```
 + CONFIRMED   TRAILING_SIGN        BIL-ADJUSTMENT-AMT
     measured  : negative_records=14,265, of=20,000, declared=S9(08)V99
 - REFUTED     TRAILING_SIGN        BIL-CHARGE-AMT
     model said: declared S9, so negatives should be present
     measured  : negative_records=0, of=20,000, declared=S9(08)V99
 - REFUTED     TRAILING_SIGN        BIL-SETTLEMENT-BALANCE
     model said: the running balance field should be signed
     measured  : reason=no such field in the copybook
 ? UNTESTABLE  FIELD_CONTAINS_PII   BIL-PACKED-TOTAL
     measured  : reason=outside the testable vocabulary
```

### The model is not the floor

Recall varies between runs. Two calls with identical inputs returned eight
proposals and five — and the five did **not** include the trailing sign on the
adjustment column, the single most expensive defect in the file.

That is why `scan` consults no model at all. The deterministic pass is the floor
and it found that defect every time. `explain` is what the model adds on top,
and it is allowed to add nothing.

`--samples N` asks more than once and takes the union, which raises recall
without guaranteeing it. The committed reply is a 3-sample union: 13 proposals,
8 confirmed, 2 inert, 3 refuted.

The model's *reading* of a field — "a credit or rebill applied against a
previously billed charge" — is kept, because it is useful, and marked
`advisory, not tested`.

### What leaves the machine

Only a **profile**: field names, PIC clauses, offsets, and counts. No record
values are transmitted for numeric fields, and character fields contribute only
a distinct-value count. There is a test that asserts this
(`test_profile_carries_no_numeric_field_values`), because on this kind of data
the claim matters more than the convenience.

Run `overpunch explain --save-prompt p.txt` to read exactly what would be sent
before sending it, or `--hypotheses file.json` to replay a saved reply and skip
the network entirely.

## Install

```bash
git clone <this repo> && cd overpunch
make demo          # generates a synthetic extract and analyses it, under a minute
make test
```

For the `explain` step, a free key from [build.nvidia.com](https://build.nvidia.com)
(no card, no paid tier):

```bash
export NVIDIA_API_KEY=nvapi-...
overpunch explain demo/UTLBILL.cpy demo/UTLBILL.dat
```

Everything except `explain` runs fully offline, with no dependencies outside the
standard library. `decode -o out.parquet` wants `pyarrow`.

## The demo data is synthetic, on purpose

`demo/make_synthetic.py` generates both the copybook and a 100,000-record EBCDIC
file with **known traps planted in it**.

Synthetic is not a compromise here, it is the requirement: because the traps are
planted, the detector can be *required* to find them. A fixture you merely
*found* can only ever show that the tool ran.

### Provenance

The trap catalogue is drawn from real experience of mainframe extracts —
these are the failures that actually happen, not a list of things that could
theoretically go wrong. **The demo schema, its domain, its field names and every
byte of its data are invented for this repository.** No copybook, record layout,
or dataset belonging to any organisation appears here, and none informed the
demo's structure.

## From copybook to a database, without guessing

```bash
overpunch plan demo/TORTURE.cpy -o plan.json    # what needs deciding
overpunch emit plan.json --format ddl           # refuses while anything is open
overpunch emit plan.json --format jsonschema
overpunch emit plan.json --format loader
```

Nothing is generated from a copybook. A **plan** is generated from the copybook,
the plan is a file a person reads, edits and reviews, and the DDL, the schema and
the loader are all rendered from the plan. Every decision the bytes cannot settle
is listed in `unresolved`, and generation refuses while any of them is open:

```
2 decision(s) in this plan are still open.

  [REDEFINES_BRANCH] redefines:TR-PAYLOAD
    TR-PAYLOAD is redefined by TR-PAYLOAD-PERSON, TR-PAYLOAD-POLICY. The same
    bytes mean different things per record and nothing in the copybook says
    which. Name the field that decides, and what its values mean.
    set "resolution" to something shaped like:
      {"discriminator": "<FIELD-NAME>", "map": {"<value>": "TR-PAYLOAD-PERSON"}}
```

`TR-DISCRIMINATOR` is obvious to a human reading that copybook. It is not
derivable *from* it — and a tool that filled it in would be right here and wrong
on the next file, with no way to tell the two apart.

The same applies to everything else the copybook leaves open, each recorded as a
policy rather than applied silently: whether `OCCURS` becomes a child table, four
flattened columns or a JSON array; whether trailing spaces are data; what counts
as NULL in a format that has no NULL; and whether `COMP-1` is IBM hexadecimal or
IEEE, which **nothing in the bytes distinguishes** — so the default is written
onto the column it affects.

## The torture record

`demo/TORTURE.cpy` is the hardest record I could write: packed decimal with an
odd digit count, three widths of binary, leading and trailing separate signs,
IBM hexadecimal floats that carry no `PICTURE` at all, a repeating group, a
variable repeating group, and two `REDEFINES` competing for the same twenty
bytes. Its expected length is computed by hand in the test file, field by field,
because a test that asks the code for the answer and then agrees with it proves
nothing.

It found four defects on first contact:

- `COMP-1`/`COMP-2` have a `USAGE` and **no `PICTURE`**. They sized to zero and
  were dropped from the field list, shortening the record by 12 bytes.
- A group with `OCCURS 4` advanced the cursor by **one** occurrence, so the
  record length and the field offsets disagreed with each other.
- IBM floats are **hexadecimal, not IEEE 754**. `0x41100000` is `1.0`; read it
  with `struct.unpack('>f')` and you get `9.0`. No error, just a wrong number.
- The copybook line itself ran past **column 72**. The compiler discards columns
  73-80, so the terminating period was thrown away, the statement swallowed the
  next line, the group inherited a `PICTURE` from what it ate, and the layout
  came out **131 bytes instead of 161** with nothing raised.

The last one is now refused outright, naming the discarded text.

## Tests are rehearsals

> A check that has never failed proves it EXECUTES, not that it CATCHES.

Every rule is exercised twice — once against a file carrying the fault it exists
for, and once against a file identical apart from the fault. A rule that fires on
both is not a detector, and the suite refuses it.

That is not decoration. Three of the first rules written here fired on files with
no fault in them at all: `TRAILING_SIGN` flagged fields with zero negative
records and an impact of `0.00`, and `WIDTH_UNDERFILL` nagged about money fields
that simply carry normal headroom. They passed every "does it find the trap"
test. Only the discrimination half caught them.

You can watch the suite work. Re-plant either fault and exactly the two
discrimination tests fail:

```
FAILED test_trailing_sign_stays_quiet_when_nothing_is_negative
FAILED test_width_underfill_does_not_nag_about_money_headroom
2 failed, 16 passed
```

## Limitations, stated plainly

- `OCCURS DEPENDING ON` reserves the maximum; genuinely variable-length records
  are not yet unpacked per-record.
- True variable-length records (`RECFM=VB` with a record descriptor word) are not
  yet unpacked; `scan` reports that the arithmetic does not work out rather than
  decoding them wrongly.
- Choosing which `REDEFINES` branch is live still needs a discriminator rule the
  tool does not yet ask for.

## The page

`docs/index.html` is a browsable version of all of this: the copybook, a hex dump
of the raw EBCDIC, a byte-level record inspector that decodes in your browser,
the findings, the model's adjudicated proposals, and the plan-to-DDL step with
its refusal.

It is **built from real tool output**, not written by hand:

```bash
make page      # regenerates the demo data, re-runs the tool, rebuilds the page
```

Every number, finding, verdict and generated schema on that page comes from
running `overpunch` against `demo/` at build time. If the tool changes its
answers, the page changes with it — which is the only way a demo stays honest.
The build refuses to write a page containing an absolute local path.

## Licence

Apache-2.0.
