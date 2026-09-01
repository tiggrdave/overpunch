(function(){
"use strict";
var D = JSON.parse(document.getElementById("payload").textContent);
var TABLES = D.encodings;
var MAX_RECORDS = 5000, MAX_BYTES = 64 * 1024 * 1024, SHOW_ROWS = 200;

var cpyText = null, cpyName = "", datBytes = null, datName = "";
var cpyAfterData = false, records = [], layout = null;

function el(t, c, x){ var e = document.createElement(t);
  if(c) e.className = c; if(x != null) e.textContent = x; return e; }
function msg(t, bad){ var m = document.getElementById("run-msg");
  m.textContent = t || ""; m.className = "rmsg" + (bad ? " bad" : ""); }
function pane(id){ return document.getElementById("tab-" + id); }
function badge(id, text, crit){
  var b = document.getElementById("b-" + id);
  b.textContent = text == null ? "" : String(text);
  b.className = "badge" + (crit ? " crit" : "");
}

function setName(which, name, bytes){
  document.getElementById("name-" + which).textContent =
    name ? name + (bytes != null ? "  (" + bytes.toLocaleString() + " bytes)" : "") : "";
  document.getElementById("drop-" + which).className = "drop" + (name ? " filled" : "");
  document.getElementById("clear-" + which).hidden = !name;
  var d = document.getElementById("drop-dat");
  if(datBytes && cpyAfterData) d.classList.add("stale"); else d.classList.remove("stale");
}

function clearOne(which){
  if(which === "cpy"){ cpyText = null; cpyName = ""; layout = null; records = []; }
  else { datBytes = null; datName = ""; cpyAfterData = false; }
  document.getElementById("in-" + which).value = "";
  setName(which, "");
  ["layout","findings","records","schema"].forEach(function(t){
    pane(t).textContent = ""; badge(t, null); });
  document.getElementById("recordpick").hidden = true;
  msg("");
}
function clearAll(){ clearOne("cpy"); clearOne("dat");
  document.getElementById("in-reclen").value = ""; msg("Cleared."); }

document.getElementById("in-cpy").onchange = function(e){
  var f = e.target.files[0]; if(!f) return;
  var r = new FileReader();
  r.onload = function(){
    cpyText = r.result; cpyName = f.name; cpyAfterData = !!datBytes;
    setName("cpy", f.name, f.size);
    msg(cpyAfterData ? "New copybook. " + datName + " is still loaded — remove it "
                     + "if you meant to start over." : "");
    analyse();
  };
  r.readAsText(f);
};
document.getElementById("in-dat").onchange = function(e){
  var f = e.target.files[0]; if(!f) return;
  if(f.size > MAX_BYTES){ msg("larger than 64 MB — analyse a slice of it", true); return; }
  var r = new FileReader();
  r.onload = function(){
    datBytes = new Uint8Array(r.result); datName = f.name; cpyAfterData = false;
    setName("dat", f.name, f.size); msg(""); analyse();
  };
  r.readAsArrayBuffer(f);
};
document.getElementById("run-analyze").onclick = function(){ analyse(); };
document.getElementById("run-clear").onclick = clearAll;
document.getElementById("clear-cpy").onclick = function(){ clearOne("cpy"); };
document.getElementById("clear-dat").onclick = function(){ clearOne("dat"); };
document.getElementById("in-enc").onchange = function(){ if(cpyText) analyse(); };
document.getElementById("in-reclen").onchange = function(){ if(cpyText) analyse(); };
document.getElementById("in-record").onchange = function(){ if(cpyText) analyse(); };
document.getElementById("run-sample").onclick = function(){
  var s = D.sample_de;
  cpyText = s.copybook; cpyName = s.copybook_name;
  var bin = atob(s.data); datBytes = new Uint8Array(bin.length);
  for(var i = 0; i < bin.length; i++) datBytes[i] = bin.charCodeAt(i);
  datName = s.data_name; cpyAfterData = false;
  setName("cpy", cpyName, cpyText.length); setName("dat", datName, datBytes.length);
  document.getElementById("in-enc").value = s.encoding || "cp037";
  document.getElementById("in-reclen").value = "";
  msg("Loaded " + s.credit + ". Switch the code page to cp037 and watch the names "
      + "break while the money stays right.");
  analyse();
};

Array.prototype.forEach.call(document.querySelectorAll("[data-tab]"), function(b){
  b.onclick = function(){
    Array.prototype.forEach.call(document.querySelectorAll("[data-tab]"), function(o){
      o.setAttribute("aria-pressed", o === b ? "true" : "false");
      pane(o.getAttribute("data-tab")).hidden = o !== b;
    });
  };
});

function fatal(where, message){
  var d = el("div", "find critical"), h = el("div", "fhead");
  h.appendChild(el("span", "chip critical", "refused"));
  h.appendChild(el("span", "fcode", where));
  d.appendChild(h); d.appendChild(el("p", "fclaim", message));
  pane("layout").textContent = ""; pane("layout").appendChild(d);
  return d;
}

function analyse(){
  ["layout","findings","records","schema"].forEach(function(t){
    pane(t).textContent = ""; badge(t, null); });
  if(!cpyText){
    pane("layout").appendChild(el("p", "empty",
      "Choose a copybook. A data file is optional — without one you get the "
      + "record layout; with one you get what the bytes actually say."));
    return;
  }

  var all;
  try { all = COBOL.parseRecords ? COBOL.parseRecords(cpyText) : [COBOL.parse(cpyText)]; }
  catch(err){ fatal("COPYBOOK", err.message); return; }

  var pick = document.getElementById("recordpick"),
      sel = document.getElementById("in-record");
  if(all.length > 1){
    if(sel.options.length !== all.length){
      sel.textContent = "";
      all.forEach(function(r, i){
        var o = document.createElement("option");
        o.value = String(i); o.textContent = r.root.name + "  (" + r.recordLen + "B)";
        sel.appendChild(o);
      });
    }
    pick.hidden = false;
    layout = all[Math.min(parseInt(sel.value, 10) || 0, all.length - 1)];
  } else { pick.hidden = true; sel.textContent = ""; layout = all[0]; }

  var override = parseInt(document.getElementById("in-reclen").value, 10);
  if(override > 0) layout.recordLen = override;
  var enc = document.getElementById("in-enc").value;

  renderLayout(override);
  if(!datBytes){
    pane("findings").appendChild(el("p", "empty",
      "Add a data file to measure the layout against it."));
    pane("records").appendChild(el("p", "empty", "Add a data file to decode records."));
    renderSchema(enc);
    msg("Layout parsed.");
    return;
  }

  SCAN.setTable(TABLES[enc]);
  var found = SCAN.recordOffsets(datBytes, layout.recordLen);
  if(found.recfm === "mismatch"){ renderMismatch(); renderSchema(enc); return; }

  var res = SCAN.scan(layout, datBytes, MAX_RECORDS);
  renderFindings(SCAN.findings(layout, res), res, found);
  renderRecords(found, enc);
  renderSchema(enc);
  msg(found.offsets.length.toLocaleString() + " records"
      + (found.recfm === "vb" ? ", RECFM=VB" : "") + ", " + enc + ".");
}

function renderLayout(override){
  var host = pane("layout");
  host.appendChild(el("div", "eyebrow", cpyName + "  ·  " + layout.recordLen
    + "-byte record" + (override > 0 ? " (overridden)" : "") + "  ·  "
    + layout.fields.length + " fields"));
  if(layout.isFragment)
    host.appendChild(el("p", "empty", "No 01 level — this is a fragment, meant to "
      + "be COPY'd into a record declared elsewhere."));
  var t = document.createElement("table"); t.className = "rec";
  var hr = document.createElement("tr");
  ["off","len","name","picture","usage"].forEach(function(x){
    var th = document.createElement("th"); th.textContent = x; hr.appendChild(th); });
  t.appendChild(hr);
  layout.fields.forEach(function(f){
    var tr = document.createElement("tr");
    [[f.offset,"n"],[COBOL.size(f)*f.occurs,"n"],[f.name,""],
     [f.pic ? f.pic.raw : "—",""],[f.usage,""]].forEach(function(c){
      var td = document.createElement("td"); td.className = c[1];
      td.textContent = c[0]; tr.appendChild(td); });
    t.appendChild(tr);
  });
  host.appendChild(t);
  badge("layout", layout.fields.length);
}

function renderMismatch(){
  var d = el("div", "find critical"), h = el("div", "fhead");
  h.appendChild(el("span", "chip critical", "critical"));
  h.appendChild(el("span", "fcode", "LAYOUT_MISMATCH"));
  d.appendChild(h);
  d.appendChild(el("p", "fclaim", "the file is not a whole multiple of this "
    + "copybook's record length, and its descriptor words do not chain to the "
    + "end either. This copybook does not describe this file."));
  var size = datBytes.length, rl = layout.recordLen;
  d.appendChild(el("div", "fev", "file_bytes=" + size.toLocaleString()
    + "   record_length=" + rl + "   remainder=" + (size % rl)));
  [[4,"a 4-byte record descriptor word (RECFM=VB)"],[8,"a block and record descriptor word"],
   [1,"a one-byte line terminator"],[2,"a two-byte line terminator (CRLF)"]].forEach(function(x){
    if(size % (rl + x[0]) === 0)
      d.appendChild(el("div","fev","— " + rl + " + " + x[0] + " = " + (rl + x[0])
        + " divides it exactly into " + (size/(rl+x[0])).toLocaleString()
        + " records, which is this record plus " + x[1]));
  });
  var exact = [];
  for(var i = 8; i <= Math.min(4096, size) && exact.length < 14; i++)
    if(size % i === 0) exact.push(i);
  if(exact.length) d.appendChild(el("div","fev",
    "— record lengths that would divide this file exactly: " + exact.join(", ")
    + ".  Set one in Record length on the left."));
  var hex = [];
  for(var j = 0; j < Math.min(8, size); j++)
    hex.push(("0" + datBytes[j].toString(16).toUpperCase()).slice(-2));
  d.appendChild(el("div","fev","— first bytes " + hex.join(" ")));
  pane("findings").appendChild(d);
  badge("findings", "!", true);
  document.querySelector('[data-tab="findings"]').click();
  msg("This copybook does not describe this file.", true);
}

function renderFindings(found, res, spans){
  var host = pane("findings");
  host.appendChild(el("div","eyebrow", datName + "  ·  "
    + spans.offsets.length.toLocaleString() + " records"
    + (spans.recfm === "vb" ? "  ·  RECFM=VB, descriptor words stripped" : "")));
  if(!found.length){
    host.appendChild(el("p","empty","No findings. Every field decodes cleanly and "
      + "nothing in the data contradicts the copybook."));
    badge("findings", 0); return;
  }
  var crit = 0;
  found.forEach(function(f){
    if(f.severity === "critical") crit++;
    var d = el("div","find " + f.severity), h = el("div","fhead");
    h.appendChild(el("span","chip " + f.severity, f.severity));
    h.appendChild(el("span","fcode", f.code));
    h.appendChild(el("span","ffield", f.field));
    d.appendChild(h);
    d.appendChild(el("p","fclaim", f.claim));
    var ev = Object.keys(f.evidence).map(function(k){ return k + "=" + f.evidence[k]; }).join("   ");
    if(ev) d.appendChild(el("div","fev", ev + "   ·   over " + f.records.toLocaleString() + " records"));
    if(f.impact) d.appendChild(el("div","fimp", f.impact));
    host.appendChild(d);
  });
  badge("findings", found.length, crit > 0);
}

function renderRecords(spans, enc){
  var host = pane("records"), n = Math.min(SHOW_ROWS, spans.offsets.length);
  host.appendChild(el("div","eyebrow","first " + n.toLocaleString() + " of "
    + spans.offsets.length.toLocaleString() + " records, decoded here"));
  var cols = layout.fields.filter(function(f){ return f.name.toUpperCase() !== "FILLER"; });
  var t = document.createElement("table"); t.className = "rec";
  var hr = document.createElement("tr");
  cols.forEach(function(f){
    var th = document.createElement("th"); th.textContent = f.name; hr.appendChild(th); });
  t.appendChild(hr);
  for(var r = 0; r < n; r++){
    var tr = document.createElement("tr"), off = spans.offsets[r];
    cols.forEach(function(f){
      var td = document.createElement("td");
      td.className = f.pic && f.pic.numeric ? "n" : "";
      td.textContent = SCAN.readValue(f, datBytes, off);
      tr.appendChild(td);
    });
    t.appendChild(tr);
  }
  host.appendChild(t);
  badge("records", spans.offsets.length.toLocaleString());
}

function renderSchema(enc){
  var host = pane("schema");
  var built = PLAN.build(layout, cpyName.replace(/\.[^.]+$/, ""), enc);
  var pre = el("pre","out");
  pre.textContent = DDL.render(built, []);
  host.appendChild(pre);
  host.appendChild(el("p","empty",
    "Columns and types only. The decisions a copybook cannot settle — which "
    + "REDEFINES branch is live, what the key is — are not guessed here any more "
    + "than they are on the command line. Run `overpunch plan` for the full "
    + "version, which refuses until you answer them."));
}

clearAll();
})();
