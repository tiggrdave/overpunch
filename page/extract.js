/* ---- extraction plan ---- */
(function(){
var P=D.plans, which="utlbill", fmt="ddl", occurs="child_table";
var answers={REDEFINES_BRANCH:"",PRIMARY_KEY:""};

function activePlan(){
  if(which==="utlbill") return P.utlbill;
  var resolved=answers.REDEFINES_BRANCH&&answers.PRIMARY_KEY;
  if(!resolved) return P.torture_open;
  return occurs==="flatten"?P.torture_flat:P.torture_child;
}
function openList(p){
  return p.plan.unresolved.filter(function(u){return !u.resolution;});
}
function renderDecisions(){
  var host=document.getElementById("decisions"); host.textContent="";
  var p=activePlan();
  var meta=p.plan.tables.length+" table(s), "+
    p.plan.tables.reduce(function(n,t){return n+t.columns.length;},0)+" column(s), "+
    p.record_bytes+"-byte records";
  document.getElementById("planmeta").textContent=meta;

  if(which==="utlbill"){
    var ok=el("div","dec answered");
    ok.appendChild(el("div","dk","nothing to decide"));
    ok.appendChild(el("div","dq","This layout has no REDEFINES and no repeating "+
      "group, so the copybook settles every question on its own. Generation "+
      "proceeds without asking."));
    host.appendChild(ok); return;
  }

  P.torture_open.plan.unresolved.forEach(function(u){
    var answered=!!answers[u.kind];
    var d=el("div","dec"+(answered?" answered":""));
    d.appendChild(el("div","dk",answered?"answered":u.kind.replace(/_/g," ")));
    d.appendChild(el("div","dq",u.question));
    var sel=document.createElement("select");
    var none=document.createElement("option");
    none.value=""; none.textContent="— not answered —"; sel.appendChild(none);
    var opts=u.kind==="REDEFINES_BRANCH"
      ? P.torture_open.plan.tables[0].columns.map(function(c){return c.field;})
      : u.options;
    opts.forEach(function(o){
      var op=document.createElement("option"); op.value=o; op.textContent=o;
      sel.appendChild(op);});
    sel.value=answers[u.kind];
    sel.onchange=function(){answers[u.kind]=sel.value;renderDecisions();renderOut();};
    d.appendChild(sel);
    host.appendChild(d);
  });

  var pol=el("div","policy");
  pol.appendChild(el("span","dk","policy · OCCURS"));
  var wrap=el("div","opts");
  [["child_table","Child table"],["flatten","Flatten to columns"]].forEach(function(o){
    var b=document.createElement("button");
    b.textContent=o[1]; b.setAttribute("aria-pressed",occurs===o[0]?"true":"false");
    b.onclick=function(){occurs=o[0];renderDecisions();renderOut();};
    wrap.appendChild(b);});
  pol.appendChild(wrap);
  host.appendChild(pol);
}
function decodedRecords(n){
  var rows=[];
  for(var i=0;i<n;i++){
    var o={},b=RECS[i];
    F.forEach(function(f){
      if(f.name==="FILLER")return;
      if(f.usage!=="DISPLAY"&&f.numeric)return;   // packed/binary omitted here
      var v=readField(b,f);
      o[f.name.toLowerCase().replace(/-/g,"_")]=f.numeric
        ? Number(v.correct.toFixed(f.scale||0)) : v.text.replace(/\s+$/,"");
    });
    rows.push(o);
  }
  return JSON.stringify(rows,null,2);
}
function renderOut(){
  var p=activePlan(),pre=document.getElementById("out"),note=document.getElementById("outnote");
  pre.className="out";
  if(fmt==="records"){
    if(which!=="utlbill"){
      pre.textContent="No data file is embedded for this layout — the torture record "+
        "exists to exercise the parser, not to carry rows.\n\nSwitch to Utility billing "+
        "to see decoded records.";
      note.textContent="";
      return;
    }
    pre.textContent=decodedRecords(3);
    note.textContent="Three records decoded in your browser from the embedded bytes. "+
      "Packed and binary fields are omitted from this view only.";
    return;
  }
  if(p.rendered.refused){
    pre.className="out refused";
    pre.textContent=p.rendered.refused;
    note.textContent="Generation is blocked, not defaulted. Answer both questions on "+
      "the left and the same command emits.";
    return;
  }
  var text=p.rendered[fmt]||"";
  if(fmt==="ddl"&&which==="torture"&&answers.PRIMARY_KEY){
    var col=answers.PRIMARY_KEY.toLowerCase().replace(/-/g,"_");
    text=text.replace(/PRIMARY KEY \(tr_account\)/,"PRIMARY KEY ("+col+")");
  }
  pre.textContent=text;
  note.textContent=which==="torture"
    ? "Recording the REDEFINES decision unblocks generation. Emitting the two "+
      "branches as separate column sets is not implemented yet — the redefined "+
      "bytes are left out rather than guessed at."
    : "Emitted straight from the plan. No question needed asking.";
}
Array.prototype.forEach.call(document.querySelectorAll("[data-layout]"),function(b){
  b.onclick=function(){
    Array.prototype.forEach.call(document.querySelectorAll("[data-layout]"),function(o){
      o.setAttribute("aria-pressed",o===b?"true":"false");});
    which=b.getAttribute("data-layout");renderDecisions();renderOut();};});
Array.prototype.forEach.call(document.querySelectorAll("[data-fmt]"),function(b){
  b.onclick=function(){
    Array.prototype.forEach.call(document.querySelectorAll("[data-fmt]"),function(o){
      o.setAttribute("aria-pressed",o===b?"true":"false");});
    fmt=b.getAttribute("data-fmt");renderOut();};});
renderDecisions();renderOut();
})();
