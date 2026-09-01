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
const COBOL = sandbox.COBOL, SCAN = sandbox.SCAN;

const refPath = path.join(here, "reference.json");
if (!fs.existsSync(refPath)) {
  console.error("reference.json missing - run: python page/reference.py");
  process.exit(2);
}
const ref = JSON.parse(fs.readFileSync(refPath, "utf8"));
SCAN.setTable(ref.cp037);

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

  const bytes = Buffer.from(c.data, "base64");
  const res = SCAN.scan(layout, bytes);
  const got = SCAN.findings(layout, res);

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

console.log(`\n${ref.cases.length} files, ${comparedFields} fields, ` +
            `${comparedFindings} findings compared, ${failures} disagreement(s)`);
if (failures) { console.log(">>> the two implementations DISAGREE"); process.exit(1); }
console.log(">>> browser and Python implementations agree");
