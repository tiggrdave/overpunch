/* Require the browser implementation to agree with the Python one.

   Run: python page/reference.py && node page/verify_js.js

   The page analyses files locally, in the visitor's browser, using a parser and
   a rule set written separately from the ones in src/. That is only trustworthy
   if the two are held to the same answers on the same bytes. */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const here = __dirname;

// The page loads these as plain <script> tags sharing one global scope, so they
// are evaluated the same way here rather than wrapped as modules.
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(here, "cobol.js"), "utf8"), sandbox);
vm.runInContext(fs.readFileSync(path.join(here, "scan.js"), "utf8"), sandbox);
vm.runInContext(fs.readFileSync(path.join(here, "ddl.js"), "utf8"), sandbox);
const COBOL = sandbox.COBOL, SCAN = sandbox.SCAN, DDL = sandbox.DDL;

const refPath = path.join(here, "reference.json");
if (!fs.existsSync(refPath)) {
  console.error("reference.json missing - run: python page/reference.py");
  process.exit(2);
}
const ref = JSON.parse(fs.readFileSync(refPath, "utf8"));


let failures = 0, comparedFields = 0, comparedFindings = 0;

for (const c of ref.cases) {
  let layout;
  try { layout = COBOL.parse(c.copybook); }
  catch (e) { console.log(`  ${c.name}: JS parser threw: ${e.message}`); failures++; continue; }

  if (layout.recordLen !== c.record_len) {
    console.log(`  ${c.name}: record length JS=${layout.recordLen} PY=${c.record_len}`);
    failures++;
  }
  for (let i = 0; i < Math.min(layout.fields.length, c.fields.length); i++) {
    const j = layout.fields[i], p = c.fields[i];
    comparedFields++;
    const jlen = COBOL.size(j) * j.occurs;
    if (j.name !== p.name || j.offset !== p.offset || jlen !== p.len || j.usage !== p.usage) {
      console.log(`  ${c.name}[${i}] JS ${j.name}@${j.offset}/${jlen} != PY ${p.name}@${p.offset}/${p.len}`);
      failures++;
    }
  }

  const page = c.encoding || "cp037";
  SCAN.setTable((ref.pages && ref.pages[page]) || ref.cp037);
  const bytes = Buffer.from(c.data, "base64");
  const res = SCAN.scan(layout, bytes);
  const got = SCAN.findings(layout, res);

  if (typeof c.records === "number" && res.records !== c.records) {
    console.log(`  ${c.name}: records read JS=${res.records} PY=${c.records}`);
    failures++;
  }

  const key = f => `${f.code}|${f.field}`;
  const jsSet = new Set(got.map(key)), pySet = new Set(c.findings.map(key));
  for (const k of pySet) if (!jsSet.has(k)) { console.log(`  ${c.name}: PY reports ${k}, JS does not`); failures++; }
  for (const k of jsSet) if (!pySet.has(k)) { console.log(`  ${c.name}: JS reports ${k}, PY does not`); failures++; }

  const pyByKey = new Map(c.findings.map(f => [key(f), f]));
  for (const f of got) {
    const p = pyByKey.get(key(f));
    if (!p) continue;
    comparedFindings++;
    if (f.severity !== p.severity) {
      console.log(`  ${c.name}: ${key(f)} severity JS=${f.severity} PY=${p.severity}`);
      failures++;
    }
    if (p.impact && f.impact && p.impact !== f.impact) {
      console.log(`  ${c.name}: ${key(f)} impact differs\n      JS: ${f.impact}\n      PY: ${p.impact}`);
      failures++;
    }
  }
}

let dialectsCompared = 0;
for (const d of ref.dialects || []) {
  dialectsCompared++;
  let lay;
  try { lay = COBOL.parse(d.copybook); }
  catch (e) { console.log(`  dialect ${d.name}: JS threw ${e.message}`); failures++; continue; }
  if (lay.recordLen !== d.record_len) {
    console.log(`  dialect ${d.name}: record length JS=${lay.recordLen} PY=${d.record_len}`);
    failures++;
  }
  if (lay.fields.length !== d.fields.length) {
    console.log(`  dialect ${d.name}: field count JS=${lay.fields.length} PY=${d.fields.length}`);
    failures++;
  }
  for (let i = 0; i < Math.min(lay.fields.length, d.fields.length); i++) {
    const j = lay.fields[i], p = d.fields[i];
    if (j.name !== p.name || j.offset !== p.offset ||
        COBOL.size(j) * j.occurs !== p.len) {
      console.log(`  dialect ${d.name}[${i}] JS ${j.name}@${j.offset} != PY ${p.name}@${p.offset}`);
      failures++;
    }
  }
}

let ddlCompared = 0;
for (const c of ref.ddl_cases || []) {
  ddlCompared++;
  // derive the key from the plan the same way postgres_ddl() does, rather than
  // being handed one the Python side never saw - the first version of this
  // harness did exactly that and reported a disagreement that was its own
  const pkDecision = (c.plan.unresolved || []).find(u => u.id === "primary_key");
  const keys = (pkDecision && pkDecision.resolution && pkDecision.resolution.primary_key) || [];
  const got = DDL.render({plan: c.plan}, keys);
  if (got !== c.ddl) {
    failures++;
    const g = got.split("\n"), p = c.ddl.split("\n");
    console.log(`  DDL ${c.name}: differs`);
    for (let i = 0; i < Math.max(g.length, p.length); i++)
      if (g[i] !== p[i]) { console.log(`      line ${i}\n        JS: ${g[i]}\n        PY: ${p[i]}`); break; }
  }
}

console.log(`\n${ref.cases.length} files, ${comparedFields} fields, ` +
            `${comparedFindings} findings, ${ddlCompared} DDL renderings, ` +
            `${dialectsCompared} dialects compared, ` +
            `${failures} disagreement(s)`);
if (failures) { console.log(">>> the two implementations DISAGREE"); process.exit(1); }
console.log(">>> browser and Python implementations agree");
