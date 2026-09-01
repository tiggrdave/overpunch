# How overpunch works

Seven diagrams. Each one is here because it carries a claim that is hard to make
in a paragraph — not to decorate the page.

The colour is consistent throughout and means one thing:

- **green** — deterministic, and proved against the bytes
- **amber** — a model, or anything not yet proved
- **red** — refused, rejected, or wrong
- **blue** — data and artefacts

---

## 1. The deterministic core

A copybook describes bytes and nothing enforces that it is telling the truth. So
the first thing that happens is an arithmetic gate: the record length derived
from the field widths must divide the data file exactly. If it does not, this
copybook does not describe this file, and nothing downstream would be
trustworthy — so the scan stops rather than reporting findings about the wrong
bytes.

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"IBM Plex Sans, system-ui, sans-serif","lineColor":"#6B7885","edgeLabelBackground":"#FFFFFF","primaryTextColor":"#17212B"}}}%%
flowchart LR
  CPY["copybook<br/><small>UTLBILL.cpy</small>"]:::data
  DAT["data file<br/><small>fixed-length EBCDIC</small>"]:::data
  P["parser<br/><small>levels, PIC, USAGE,<br/>OCCURS, REDEFINES</small>"]:::det
  L["layout<br/><small>offsets + widths</small>"]:::det
  G{"record length<br/>divides the file?"}:::det
  STOP["refuse<br/><small>LAYOUT_MISMATCH</small>"]:::bad
  PR["prober<br/><small>counts over every record</small>"]:::det
  R["rules<br/><small>one shared definition</small>"]:::det
  F["findings<br/><small>each with its record count<br/>and the arithmetic</small>"]:::det

  CPY --> P --> L --> G
  DAT --> G
  G -- no --> STOP
  G -- yes --> PR --> R --> F
  DAT --> PR

  classDef det  fill:#DDEFE7,stroke:#1E7A5B,color:#123C2D;
  classDef data fill:#E4EEF3,stroke:#2F6F8F,color:#12333F;
  classDef bad  fill:#F6DEDC,stroke:#A5322C,color:#4A1614;
```

---

## 2. Nemotron proposes, the bytes dispose

The model is genuinely good at reading forty-year-old English hiding in field
names. It is not good at being believed. So it may only propose from a **closed
vocabulary** of eleven hypothesis kinds the prober knows how to test, and every
proposal is adjudicated against the actual bytes.

The consequence worth stating plainly: **a hallucination cannot become a
finding.** It can only become a refuted hypothesis.

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"IBM Plex Sans, system-ui, sans-serif","lineColor":"#6B7885","edgeLabelBackground":"#FFFFFF","primaryTextColor":"#17212B"}}}%%
flowchart LR
  PROF["profile<br/><small>names, PICs, counts —<br/>no field values leave</small>"]:::data
  NEM["Nemotron 3 Super<br/><small>via NIM</small>"]:::model
  H["hypotheses<br/><small>from a closed vocabulary</small>"]:::model
  ADJ["adjudicator<br/><small>tests each against the bytes</small>"]:::det
  C["CONFIRMED"]:::det
  I["INERT<br/><small>true, but changes<br/>no value here</small>"]:::data
  RF["REFUTED<br/><small>the model was wrong<br/>and the file said so</small>"]:::bad
  U["UNTESTABLE<br/><small>outside the vocabulary —<br/>printed as an opinion</small>"]:::model
  FIND["finding"]:::det

  PROF --> NEM --> H --> ADJ
  ADJ --> C --> FIND
  ADJ --> I
  ADJ --> RF
  ADJ --> U

  classDef det   fill:#DDEFE7,stroke:#1E7A5B,color:#123C2D;
  classDef model fill:#F6E9D2,stroke:#A9711A,color:#4A3208;
  classDef data  fill:#E4EEF3,stroke:#2F6F8F,color:#12333F;
  classDef bad   fill:#F6DEDC,stroke:#A5322C,color:#4A1614;
```

---

## 3. Reading a copybook that is only a photograph

Many copybooks are printouts. `nemotron-parse` reads the page — and gets it
wrong regularly: six reads of one image gave four correct layouts and two wrong
ones, and **both wrong ones parsed as valid COBOL with a plausible field list.**

What makes that survivable is that a copybook makes a falsifiable prediction.
The check is decisive and cheap, so the honest way to use an unreliable reader
is to re-read until a layout is proved.

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"IBM Plex Sans, system-ui, sans-serif","lineColor":"#6B7885","edgeLabelBackground":"#FFFFFF","primaryTextColor":"#17212B"}}}%%
flowchart LR
  IMG["scanned page<br/><small>no text layer</small>"]:::data
  NP["nemotron-parse"]:::model
  RE["reassemble<br/><small>LaTeX table → COBOL</small>"]:::model
  PARSE["parse"]:::det
  CH{"widths sum to the<br/>printed RECLN?<br/>file divides by it?"}:::det
  OK["proved<br/><small>use this layout</small>"]:::det
  NO["rejected<br/><small>plausible, parseable,<br/>and wrong</small>"]:::bad

  IMG --> NP --> RE --> PARSE --> CH
  CH -- yes --> OK
  CH -- no --> NO
  NO -. "re-read" .-> NP

  classDef det   fill:#DDEFE7,stroke:#1E7A5B,color:#123C2D;
  classDef model fill:#F6E9D2,stroke:#A9711A,color:#4A3208;
  classDef data  fill:#E4EEF3,stroke:#2F6F8F,color:#12333F;
  classDef bad   fill:#F6DEDC,stroke:#A5322C,color:#4A1614;
```

---

## 4. Nothing is generated from a copybook

A schema needs answers the bytes do not carry: which `REDEFINES` branch is live,
whether a repeating group flattens or normalises, what the key is. So a **plan**
is generated instead, a person edits it, and every target is rendered from the
plan. Generation is blocked while any decision is open.

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"IBM Plex Sans, system-ui, sans-serif","lineColor":"#6B7885","edgeLabelBackground":"#FFFFFF","primaryTextColor":"#17212B"}}}%%
flowchart LR
  CPY["copybook"]:::data
  PLAN["extraction plan<br/><small>reviewable, diffable</small>"]:::det
  Q{"any decision<br/>still open?"}:::det
  ASK["refuse, and say what<br/>needs deciding<br/><small>REDEFINES branch,<br/>primary key</small>"]:::bad
  HUMAN["a person answers"]:::data
  DDL["PostgreSQL DDL"]:::det
  JS["JSON Schema"]:::det
  LD["loader script"]:::det

  CPY --> PLAN --> Q
  Q -- yes --> ASK --> HUMAN --> PLAN
  Q -- no --> DDL
  Q -- no --> JS
  Q -- no --> LD

  classDef det  fill:#DDEFE7,stroke:#1E7A5B,color:#123C2D;
  classDef data fill:#E4EEF3,stroke:#2F6F8F,color:#12333F;
  classDef bad  fill:#F6DEDC,stroke:#A5322C,color:#4A1614;
```

---

## 5. Where the sign actually lives

The subtlest bug in the project. EBCDIC is a *family* of national code pages, and
`0xD0` is `}` in cp037, `ü` in cp273 and `ğ` in cp1026. Reading the sign from the
decoded **character** works on US data and silently loses German negatives. The
zone nibble of the byte is the layer every page agrees on.

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"IBM Plex Sans, system-ui, sans-serif","lineColor":"#6B7885","edgeLabelBackground":"#FFFFFF","primaryTextColor":"#17212B"}}}%%
flowchart LR
  B["final byte<br/>of the field"]:::data
  Z{"zone nibble"}:::det
  NEG["negative<br/><small>digit = low nibble</small>"]:::det
  POS["positive<br/><small>digit = low nibble</small>"]:::det
  UNS["unsigned"]:::det
  T{"trailing<br/>+ or - ?"}:::det
  ASC["ASCII separate sign"]:::det
  PLAIN["no sign"]:::data

  B --> Z
  Z -- "0xD_" --> NEG
  Z -- "0xC_" --> POS
  Z -- "0xF_" --> UNS
  Z -- "anything else" --> T
  T -- yes --> ASC
  T -- no --> PLAIN

  classDef det  fill:#DDEFE7,stroke:#1E7A5B,color:#123C2D;
  classDef data fill:#E4EEF3,stroke:#2F6F8F,color:#12333F;
```

---

## 6. Two implementations, held to the same answers

The page analyses your files **in your browser**, because the data this tool
exists for is exactly the data nobody may upload. That means the parser and the
rules exist twice — and the second one is written separately rather than ported,
so that disagreement is informative.

It has earned its keep three times: the digit-dropping bug in the Python
decoder, the `", "` separators in the DDL header, and the packed values the
browser was validating but never reading.

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"IBM Plex Sans, system-ui, sans-serif","lineColor":"#6B7885","edgeLabelBackground":"#FFFFFF","primaryTextColor":"#17212B"}}}%%
flowchart LR
  subgraph PY["Python — src/overpunch/"]
    P1["copybook.py"]:::det
    P2["probe.py + findings.py"]:::det
    P3["generate.py"]:::det
  end
  subgraph JS["JavaScript — page/"]
    J1["cobol.js"]:::det
    J2["scan.js"]:::det
    J3["ddl.js"]:::det
  end
  V{"verify_js.js<br/><small>same files, same bytes</small>"}:::det
  AGREE["agree<br/><small>lengths, offsets, codes,<br/>severities, money to the cent</small>"]:::det
  FAIL["one of them is wrong"]:::bad

  P1 --> V
  P2 --> V
  P3 --> V
  J1 --> V
  J2 --> V
  J3 --> V
  V -- "0 differences" --> AGREE
  V -- "any difference" --> FAIL

  classDef det fill:#DDEFE7,stroke:#1E7A5B,color:#123C2D;
  classDef bad fill:#F6DEDC,stroke:#A5322C,color:#4A1614;
```

---

## 7. The modules

`predicates.py` exists because `scan` and `explain` are two callers asking the
same questions of the same measurements, and when each carried its own copy they
drifted.

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"IBM Plex Sans, system-ui, sans-serif","lineColor":"#6B7885","edgeLabelBackground":"#FFFFFF","primaryTextColor":"#17212B"}}}%%
flowchart LR
  layout["layout.py<br/><small>fields, widths, offsets</small>"]:::det
  copybook["copybook.py<br/><small>parser + guards</small>"]:::det
  decode["decode.py<br/><small>EBCDIC, overpunch,<br/>packed, IBM hex float</small>"]:::det
  probe["probe.py<br/><small>evidence over the bytes</small>"]:::det
  pred["predicates.py<br/><small>one definition per rule</small>"]:::det
  find["findings.py"]:::det
  plan["plan.py<br/><small>and what it refuses</small>"]:::det
  gen["generate.py<br/><small>DDL, schema, loader</small>"]:::det
  explain["explain.py"]:::model
  vision["vision.py"]:::model
  cli["cli.py"]:::data

  copybook --> layout
  decode --> layout
  probe --> decode
  probe --> layout
  pred --> probe
  find --> pred
  plan --> layout
  gen --> plan
  explain --> pred
  vision --> copybook
  cli --> find
  cli --> gen
  cli --> explain
  cli --> vision

  classDef det   fill:#DDEFE7,stroke:#1E7A5B,color:#123C2D;
  classDef model fill:#F6E9D2,stroke:#A9711A,color:#4A3208;
  classDef data  fill:#E4EEF3,stroke:#2F6F8F,color:#12333F;
```
