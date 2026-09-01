/* Build a minimal extraction plan in the browser.

   A second implementation of the column and type mapping in
   src/overpunch/plan.py, so the tool page can show a schema without a server.
   page/verify_js.js compares every type it produces against the Python across
   every copybook in the repository.

   It is deliberately only the columns: the DECISIONS - which REDEFINES branch
   is live, what the key is - are not guessed here any more than they are there.
   The page says so, and points at `overpunch plan` for the full version. */
var PLAN = (function(){
"use strict";

var TRANSLITERATE = {"Ä":"AE","Ö":"OE","Ü":"UE","ß":"SS",
  "Å":"AA","Æ":"AE","Ø":"OE","Þ":"TH","Ð":"DH",
  "Œ":"OE","İ":"I","ı":"i","Ł":"L"};

function normalise(name){
  var s = name;
  Object.keys(TRANSLITERATE).forEach(function(ch){
    var repl = TRANSLITERATE[ch];
    s = s.split(ch).join(repl).split(ch.toLowerCase()).join(repl.toLowerCase());
  });
  s = s.normalize("NFKD").replace(/[̀-ͯ]/g, "");
  s = s.replace(/[^0-9a-zA-Z]+/g, "_").replace(/^_+|_+$/g, "").toLowerCase();
  if (/^[0-9]/.test(s)) s = "c_" + s;
  return s || "unnamed";
}

var INT_TYPES = [[4,"SMALLINT"],[9,"INTEGER"],[18,"BIGINT"]];

function sqlType(f, trim){
  if (f.usage === "COMP-1") return "REAL";
  if (f.usage === "COMP-2") return "DOUBLE PRECISION";
  var p = f.pic;
  if (!p) return "BYTEA";
  if (!p.numeric) return (trim === false ? "CHAR" : "VARCHAR") + "(" + p.chars + ")";
  if (p.scale) return "NUMERIC(" + p.digits + "," + p.scale + ")";
  for (var i = 0; i < INT_TYPES.length; i++)
    if (p.digits <= INT_TYPES[i][0]) return INT_TYPES[i][1];
  return "NUMERIC(" + p.digits + ")";
}

function build(layout, table, encoding){
  var columns = [], seen = {};
  layout.fields.forEach(function(f){
    if (f.name.toUpperCase() === "FILLER") return;
    var ident = normalise(f.name);
    if (seen[ident]) return;                 // a collision is a decision, not a guess
    seen[ident] = true;
    columns.push({field: f.name, name: ident, type: sqlType(f, true),
                  offset: f.offset, bytes: COBOL.size(f) * f.occurs, note: ""});
  });
  return {plan: {
    source: {copybook: table + ".cpy", record_bytes: layout.recordLen,
             encoding: encoding || "cp037"},
    policies: {identifier_style: "snake_case", trim_trailing_spaces: true,
               null_when: ["low_values"], float_encoding: "ibm_hex",
               occurs: "flatten"},
    target: {dialect: "postgresql", table: normalise(table)},
    unresolved: [],
    tables: [{name: normalise(table), kind: "root", columns: columns}]}};
}

return {build: build, sqlType: sqlType, normalise: normalise};
})();
