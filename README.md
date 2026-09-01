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

## Why a model has to be challenged

Every other component in a system fails loudly. A disk returns an error, a parser
raises, a network call times out — you learn something from the failure itself. A
model does not do that. **Its failure mode is a confident, well-formed, plausible
answer**, which at the point of use is indistinguishable from a correct one.

That property invalidates most of how trust is normally established. You cannot
use the model's own confidence, because it is not correlated with correctness.
You cannot use a second model as the judge, because it shares the failure modes
of the first — that is checking eyes with eyes.

So the question becomes: what do you have that the model does not influence?
Usually something duller than a model and far more reliable. Here it is
arithmetic:

- the field widths must sum to the record length printed on the page
- the data file must divide by that length exactly
- a proposed key must be unique across every record
- a proposed discriminator must make its branch fit the records it claims better
  than the records it does not

None of those are clever. All of them are decisive, and none can be talked around.

### A check is only worth having if it could have failed

This is the part that gets skipped. A check that has never rejected anything
proves it EXECUTES, not that it CATCHES — and that caught me four separate times
while building this:

- the cross-check between the two implementations compared findings but never
  record counts, so a browser that read **zero** records agreed with a Python that
  read thirty; both produce an empty finding list
- the branch adjudicator compared the wrong axis and **rejected the correct
  answer**
- twice a rehearsal reported success when the fault it was meant to plant had
  never actually been written to the file

Every one of those ran, passed, and told me nothing. The fix is not more checks;
it is requiring each check to fail on demand.

### The evidence is not theoretical

`nemotron-parse` read the same scanned copybook six times and got it right four.
**Both wrong readings parsed as valid COBOL with a sensible field list** — nothing
about their shape gave them away; only the arithmetic did.

`nemotron-3-super`, given byte-identical input twice, returned eight proposals and
then five, and the five omitted the single most expensive defect in the file.

It also found a real bug in *this* code: it kept puzzling over a
`distinct_values: 0` that turned out to be a figure reported for numeric fields
and never measured for them. A false zero, not a missing one. Challenging a model
productively means being willing to lose the argument.

### The payoff is usefulness, not safety

Because every proposal is adjudicated, the same machinery measures models instead
of praising them. `overpunch benchmark` scores them against what the deterministic
pass finds on its own: Nemotron 3 Super reaches **100% precision and 53% recall**
on this task, while the 550B Ultra is no better and three times slower. You can
only benchmark what you can mark.

Which inverts the usual anxiety. **An unreliable component plus a decisive check
is a reliable system** — the same trade as a checksum or a retransmit. Once the
check is cheap and conclusive you can re-read until a result is proved and report
how many attempts it took, which is exactly what `read-scan --attempts` does.

The honest boundary: not everything is checkable. The model's plain-English
reading of what a field *means* is useful and unverifiable. It is shown and
labelled `advisory, not tested`, because the alternative is laundering an opinion
into a finding — and a system that does that once cannot be trusted anywhere else.

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
derivable *from* it — which is exactly the gap a language model fills, and
exactly why its answer cannot be taken on trust.

```bash
overpunch resolve plan.json --data demo/TORTURE.dat --apply
```

Nemotron answers the questions; the **data** decides whether it was right:

```
 + CONFIRMED   REDEFINES_BRANCH  redefines:TR-PAYLOAD
     proposed  : {"discriminator": "TR-DISCRIMINATOR",
                  "map": {"P": "TR-PAYLOAD-PERSON", "L": "TR-PAYLOAD-POLICY"}}
     measured  : TR-PAYLOAD-PERSON: own 100% vs other 100%
                 TR-PAYLOAD-POLICY: own 100% vs other 33%

 + CONFIRMED   PRIMARY_KEY       primary_key
     proposed  : {"primary_key": ["TR-REGION", "TR-ACCOUNT"]}
     measured  : distinct=300, records=300, duplicates=0, blank=0
```

Both checks are **two-sided**, because a plausible guess passes a one-sided one:

- a proposed key must be **unique across every record**. `TR-ACCOUNT` alone is
  the obvious answer and it is wrong — the same account numbers occur in both
  regions, so it yields 150 distinct values for 300 records and is refuted.
- a proposed discriminator must make each branch fit the records it **claims**
  better than the records it does not. Comparing branches against each other on
  one value does not work: `TR-PAYLOAD-PERSON` is all `PIC X`, so a policy
  number is perfectly good text to it and it scores 100% on everything. The
  first version of this check did that, and rejected the correct answer.

`--apply` writes back **only confirmed** resolutions, recording which model
proposed each one and the evidence that survived. Refuted proposals are never
written. The tool still does not decide — it does the legwork, tries hard to
prove itself wrong, and asks you to confirm.

The same applies to everything else the copybook leaves open, each recorded as a
policy rather than applied silently: whether `OCCURS` becomes a child table, four
flattened columns or a JSON array; whether trailing spaces are data; what counts
as NULL in a format that has no NULL; and whether `COMP-1` is IBM hexadecimal or
IEEE, which **nothing in the bytes distinguishes** — so the default is written
onto the column it affects.

## When a file will not divide

`LAYOUT_MISMATCH` used to be a dead end: *"this copybook does not describe this
file"*, and nothing more. But the file size is a hard constraint — **only its
divisors can be the record length** — so the tool now says which they are, and
whether one of them is this copybook's record plus a header:

```
[CRITICAL] LAYOUT_MISMATCH
    evidence: file_bytes=57,646  record_length=70  remainder=36
    - 70 + 4 = 74 divides it exactly into 779 records, which is this record
      plus a 4-byte record descriptor word (RECFM=VB)
    - record lengths that would divide this file exactly: 19, 37, 38, 41, 74,
      82, 703, 779, 1406, 1517, 1558, 3034
```

That is a real diagnosis rather than a refusal, and it came from watching
someone load a real 57,646-byte file, get the refusal, and have nowhere to go.

**`RECFM=VB` is now read**, in both implementations. Each record carries a
4-byte descriptor word — a big-endian length including the RDW, then two
reserved zero bytes — and detection requires a *real* descriptor, not merely a
size that happens to divide by `record_length + 4`. A descriptor that stops
making sense mid-file raises, because once the reader is out of step every
record after it is plausible nonsense.

## Diagrams

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — seven diagrams, each carrying a
claim that is hard to make in a paragraph: the deterministic core and its
arithmetic gate, the propose/adjudicate split, the read-until-proved loop for
scanned copybooks, why nothing is generated straight from a copybook, where the
sign actually lives, the two implementations and the bridge between them, and
the module graph.

They render natively on GitHub. Rendered PNGs are in `docs/diagrams/` for slides.

## The sample corpus

```bash
make samples          # generates samples/data/, nothing binary is committed
pytest tests/test_samples.py
```

One file per thing that can go wrong, each with its expected findings written
down in `samples/MANIFEST.json` **before** the tool is run:

| sample | what it is for | expected |
|---|---|---|
| `clean` | a file with nothing wrong with it | *no findings at all* |
| `ascii-separate-sign` | not every extract is EBCDIC; some carry a real `+`/`-` | TRAILING_SIGN, IMPLIED_DECIMAL |
| `packed-heavy` | COMP-3 money, two digits a byte | IMPLIED_DECIMAL |
| `corrupt-packed` | a COMP-3 field that is not actually packed | INVALID_PACKED, IMPLIED_DECIMAL |
| `wrong-copybook` | a copybook that does not describe the file | *refuses to scan* |
| `variable-blocked` | RECFM=VB, with a record descriptor word | *refuses to scan* |
| `never-populated` | a reserved field nobody ever used, and an uncovered code |  NEVER_POPULATED, UNCOVERED_VALUE |

Plus `demo/UTLBILL` (the teaching case), `demo/TORTURE` (every awkward
construct), a German cp273 file built in `tests/test_codepages.py`, a scanned
copybook under `demo/scans/`, and the real AWS CardDemo files.

`clean` is the one that matters most. Every other sample checks that something
*is* found; that one checks that nothing is, which is what stops the rules
drifting into noise.

**Building the corpus immediately found a gap.** `packed-heavy` produced no
findings at all, because `IMPLIED_DECIMAL` only fired on `DISPLAY` fields - so a
file of signed, scaled `COMP-3` money carrying exactly the same 100x trap got
silence. A decoder that returns the packed integer and leaves scaling to the
caller is the common case, and the caller routinely forgets. Now reported for
any usage.

## Against a real mainframe application

Everything else here is synthetic by design. This is the control:

```bash
python scripts/fetch_carddemo.py     # nothing is vendored; it downloads on demand
overpunch scan demo/carddemo/CVTRA06Y.cpy demo/carddemo/DALYTRAN.PS
```

[AWS CardDemo](https://github.com/aws-samples/aws-mainframe-modernization-carddemo)
is a COBOL credit-card system published by AWS, with genuine EBCDIC data and the
copybooks describing it, written by people who had never heard of this tool.

`CVACT01Y.cpy` carries `RECLN 300` in a comment this parser does not read. The
parser reaches **300 bytes from the field widths alone**, and the data file is
exactly 50 records of it. Two independent sources agreeing, neither of them us.

On the daily transaction file it finds this:

```
[CRITICAL] TRAILING_SIGN        DALYTRAN-AMT
    evidence: negative=50, positive=250, share_negative=16.7%
    impact: correct total 104,801.54; sign ignored 153,600.12
            (overstated by 48,798.58)
```

**A 47% overstatement on a public dataset.** And the sign is corroborated from a
field 100 bytes away: every one of those 50 records describes a *"Return item
at..."*. The amount's sign comes from one byte; the word "Return" comes from
somewhere else entirely. Agreement between them is evidence, not self-consistency.

The account file, by contrast, produces **no critical finding at all** - it has no
negative balances, so the tool says so quietly. A detector that finds something
alarming in every file is not a detector.

One honest negative result: **record-length arithmetic alone does not identify
the right copybook.** Every CardDemo file divides evenly by six of these record
lengths. Divisibility rules layouts out; only content rules one in.

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

## Pointed at 885 real copybooks

A production government tax system's entire copybook library. The first run
parsed **727**. After the defects below it parses **861**, and the remaining 24
are correctly identified as **procedure-division code** — COPY members holding
executable statements, with no record layout in them at all.

Across those 861 there are **1,270 record layouts**, because 72 copybooks
declare more than one. Median record 96 bytes, longest 200,030 — a
`PIC S9(03) COMP-3 OCCURS 99999 TIMES` working-storage table, which is
199,998 + 32 and exactly right.

Two more defects it found, on top of the five below:

- **`OCCURS` on an elementary field advanced the cursor five times too far.**
  `PIC X(40) OCCURS 5` moved 1,000 bytes instead of 200, because the offset walk
  returned a size that already included `OCCURS` and the caller multiplied
  again. Every `OCCURS` in my own torture record is on a *group*, so this path
  had never been taken. 69 copybooks.
- **A copybook may declare several records.** An `01` is a record boundary, not
  a continuation; treating the second as a field under the first raised
  "orphaned level 1" and silently lost everything after it. 65 copybooks, one of
  them declaring 24 records.

## Pointed at twelve real copybooks, all twelve failed

They came from a production government tax system. Nine returned a **zero-byte
record with no error at all**; three raised. Five separate defects, every one of
them a convention this parser had simply never met:

| what real copybooks do | what happened |
|---|---|
| a **five-digit** sequence number, `00001 ` | only six digits were recognised, so every comment parsed as a statement, `00001` became a level number, and the record came out **0 bytes** |
| `SKIP1`, `SKIP2`, `EJECT` listing directives | no terminating period, so each one merged with the next line and **swallowed the field after it** |
| **no `01` level** — a fragment to be `COPY`'d into a record declared elsewhere | refused outright. All twelve started at `05` or `10`; requiring `01` rejects the common case |
| `VALUE -9999999.99.` | split at the **decimal point**: the statement ended early and `99` was parsed as a level-99 field |
| `REDEFINES` a record from **another copybook** | nothing here to share bytes with, so the layout collapsed to zero |

All twelve parse now, and the arithmetic was hand-checked against one of them:
58 bytes summed by hand, 58 from the parser, three internal `REDEFINES` correctly
sharing bytes and the external one **reported as an assumption** rather than
silently absorbed.

The browser parser had every one of the same defects, which would have made
"try it on your own files" fail for precisely the people it is aimed at. Both
implementations were fixed and now agree on all twelve — 3,243 fields, zero
disagreements — and on five synthetic fixtures of these conventions that are
part of the permanent cross-check.

None of that source appears in this repository.

## What the tests actually check

199 tests, in five kinds:

| kind | what it holds | example |
|---|---|---|
| **rehearsals** | every rule must fire on a planted fault *and* stay quiet without it | re-plant either sign bug and exactly the right tests fail |
| **corpus** | each sample produces the findings its manifest declared beforehand | `samples/MANIFEST.json` |
| **properties** | invariants over every value, page and copybook | encode→decode round-trips across four code pages and every width |
| **metamorphic** | doubling a file doubles every total; reordering changes none | catches state leaking between records |
| **differential** | Python and JavaScript held to identical answers | 6 files, 74 fields, 39 findings, 4 DDL renderings |

Plus boundaries — empty files, a file one byte short, single records, all-`0x00`,
all-`0xFF` — and structural invariants asserted over every copybook in the
repository: no two fields overlap unless one redefines the other, no field
reaches past the record, no finding names a field the layout does not contain.

**Writing them found four more defects**: a reader that loaded whole files into
memory (20 MB resident for a 21 MB file, fatal on the gigabyte extracts this
tool is for), packed and binary values that were validated but never decoded,
`IMPLIED_DECIMAL` exempting every non-`DISPLAY` field, and identifiers being
mangled rather than transliterated — `GEBÜRTSDATUM` became `geb_rtsdatum`.

### Still not covered, and worth knowing

- **Mutation testing at scale.** Faults are planted by hand, one at a time.
- **A genuinely independent oracle.** Both implementations are mine. Comparing
  against Cobrix or JRecord would be independent in a way this is not.
- **Breadth of real copybooks.** One real source (AWS CardDemo), not ten.
- **Systematic parser fuzzing.** Malformed COBOL is tested by example only.
- **True `OCCURS DEPENDING ON`.** The maximum is reserved; variable-length
  records are not unpacked.

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
- Choosing which `REDEFINES` branch is live still needs a discriminator rule the
  tool does not yet ask for.

## Two pages, because there are two jobs

- **[the demo](https://tiggrdave.github.io/overpunch/)** — the argument, read top
  to bottom: what goes wrong, what it costs, a byte inspector, a scanned copybook,
  the plan that refuses. Linear, because that is how someone meets this for the
  first time.
- **[the inspector](https://tiggrdave.github.io/overpunch/inspect.html)** — the
  tool. Sidebar for the copybook, the data file, the code page and the record
  length; tabs for Layout, Findings, Records and Schema. **84 KB against the
  demo's 302 KB**, because it carries none of the demo's embedded data.

They wanted opposite layouts and one page was making both worse. Splitting them
came out of using the thing: getting to the uploader meant scrolling past ten
screens of essay every time.

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

## When the copybook is a photograph

Plenty of copybooks are not files. They are printouts, or images inside a PDF
nobody can select text out of, and the person who could retype them left in 2009.

```bash
overpunch read-scan demo/scans/CVTRA06Y-scan.png \
    --data demo/carddemo/DALYTRAN.PS --attempts 4
```

`nvidia/nemotron-parse` reads the page. Nothing downstream believes it:

```
what could be PROVED about it, against the bytes:
  [PASS] parses as COBOL                        14 fields, 350-byte record
  [PASS] matches the length printed on the page parsed 350 from the field widths,
                                                page says 350
  [PASS] divides the real data file exactly     105,000 / 350 = 300.0000

A model read the page. The arithmetic decided whether it read it right.
```

**It gets it wrong regularly.** Six reads of the same image gave four correct
layouts and two wrong ones — a run where `DALYTRAN-ID` came back as
`DALYTRANS-ID` and `DALYTRAN-TYPE-CD` was truncated to `DALY`, and another at
319 bytes. Both of the wrong ones **parsed as valid COBOL with a sensible field
list.** Nothing about their shape gives them away.

What makes that survivable is that a copybook makes a falsifiable prediction:
the field widths must sum to the record length printed on the page, and the real
data file must divide by it exactly. So the checks are decisive and cheap, and
the honest way to use an unreliable reader is to **re-read until a layout is
proved** and say how many attempts it took.

The chain closes: a picture of a printout produces a copybook that finds the
*same* defect in the real data as the genuine one, to the cent —
`correct total 104,801.54; sign ignored 153,600.12`. There is a test asserting
exactly that, and two more asserting the bad read is rejected.

Both fixtures under `tests/fixtures/` are real captured replies, so the tests
run without a network or a key.

## EBCDIC is a family, and the sign is not in the text

COBOL's reserved words are English everywhere - `PICTURE`, `OCCURS`, `REDEFINES`
are the same in Frankfurt as in Ohio. Field names and comments are not: a German
copybook is full of `KUNDEN-NR` and `GEBURTSDATUM`. Neither of those troubles a
parser.

The code page does. EBCDIC is a family of national variants - **cp273** German,
**cp500** international, **cp1026** Turkish, **cp870** Central European - and
they disagree about exactly the bytes that matter:

| byte | cp037 | cp273 | cp1026 |
|---|---|---|---|
| `0xD0` | `}` | `ü` | `ğ` |
| `0xC0` | `{` | `ä` | `ç` |

`0xD0` is the trailing overpunch for **negative zero**. An earlier version of
this decoder read the sign from the *decoded character*, looking for `}`. On
German data it therefore did not recognise the sign at all: the digit was
dropped and the record silently turned positive. Measured on a 200-record file,
that moved the reported total by **122,228**, with no warning of any kind.

The sign lives in the **zone nibble of the byte** - `0xC_` positive, `0xD_`
negative - and every EBCDIC page agrees on that. It is read from the byte now,
so the money is identical whichever page you decode the text with:

```
cp273   negative=54  correct total 1,416,732.81
cp037   negative=54  correct total 1,416,732.81
cp1026  negative=54  correct total 1,416,732.81
```

Only `-0` was affected, because it is the one digit whose sign byte differs
between pages. Nine negatives in ten decoded correctly, which is precisely why
nobody would have noticed.

## Two implementations, held to the same answers

The page analyses files **in your browser**. That is not a convenience: the data
this tool exists for — benefit records, tax extracts, card transactions — is
exactly the data nobody may upload to a website, so the analysis has to come to
the bytes rather than the other way round. Disconnect your network and it still
works.

Which means the copybook parser and the rules exist twice: `src/overpunch/` in
Python and `page/cobol.js` + `page/scan.js` in JavaScript. They are written
separately, on purpose, and held to the same answers:

```bash
make verify-js
```

runs both over every copybook and data file in the repository and fails on any
disagreement — record length, field offsets, finding codes, severities, and the
exact money totals in the impact lines. Currently **10 copybooks, 133 fields,
5 data files, 35 findings, 0 disagreements**.

This is not ceremony. Writing the decoder a second time in a different language
is what found the digit-dropping bug in the first one; neither implementation's
own tests had caught it.

## Licence

Apache-2.0.
