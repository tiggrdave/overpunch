/* ---- analyse a copybook (and optionally a data file) locally ---- */
(function(){
var cpyText=null, cpyName="", datBytes=null, datName="";
var TABLES=D.encodings;
var MAX_RECORDS=5000, MAX_BYTES=32*1024*1024;

function setName(which,name,bytes){
  document.getElementById("name-"+which).textContent =
    name ? name + (bytes!=null ? "  ("+bytes.toLocaleString()+" bytes)" : "") : "";
  document.getElementById("drop-"+which).className = "drop"+(name?" filled":"");
}
function msg(t,bad){
  var m=document.getElementById("run-msg");
  m.textContent=t||""; m.className="rmsg"+(bad?" bad":"");
}
document.getElementById("in-cpy").onchange=function(e){
  var f=e.target.files[0]; if(!f)return;
  var r=new FileReader();
  r.onload=function(){ cpyText=r.result; cpyName=f.name; setName("cpy",f.name,f.size); msg(""); };
  r.readAsText(f);
};
document.getElementById("in-dat").onchange=function(e){
  var f=e.target.files[0]; if(!f)return;
  if(f.size>MAX_BYTES){ msg("that file is larger than 32 MB — analyse a slice of it instead",true); return; }
  var r=new FileReader();
  r.onload=function(){ datBytes=new Uint8Array(r.result); datName=f.name;
    setName("dat",f.name,f.size); msg(""); };
  r.readAsArrayBuffer(f);
};
function loadSample(sample){
  cpyText=sample.copybook; cpyName=sample.copybook_name;
  var bin=atob(sample.data); datBytes=new Uint8Array(bin.length);
  for(var i=0;i<bin.length;i++) datBytes[i]=bin.charCodeAt(i);
  datName=sample.data_name;
  setName("cpy",cpyName,cpyText.length); setName("dat",datName,datBytes.length);
  document.getElementById("in-enc").value=sample.encoding||"cp037";
  msg("Loaded "+sample.credit); analyse();
}
document.getElementById("run-sample").onclick=function(){ loadSample(D.sample); };
var de=document.getElementById("run-sample-de");
if(de) de.onclick=function(){
  loadSample(D.sample_de);
  msg(msgText()+"  — now switch the code page to cp037 and watch the names break "+
      "while the money stays right.");
};
function msgText(){ return document.getElementById("run-msg").textContent; }
document.getElementById("run-analyze").onclick=analyse;

/* Only the divisors of the file size can be the record length. Saying which,
   and whether one is this record plus a header, turns a dead end into a lead. */
function explainMismatch(size, recordLen){
  var notes = [];
  [[4,"a 4-byte record descriptor word (RECFM=VB)"],
   [8,"a block and record descriptor word (RECFM=VBS)"],
   [1,"a one-byte line terminator"],
   [2,"a two-byte line terminator (CRLF)"]].forEach(function(x){
    if(size % (recordLen + x[0]) === 0)
      notes.push(recordLen+" + "+x[0]+" = "+(recordLen+x[0])+" divides it exactly into "+
        (size/(recordLen+x[0])).toLocaleString()+" records, which is this record plus "+x[1]);
  });
  var exact = [];
  for(var i = 8; i <= Math.min(4096, size) && exact.length < 14; i++)
    if(size % i === 0) exact.push(i);
  if(exact.length) notes.push("record lengths that would divide this file exactly: "+exact.join(", "));
  if(!notes.length) notes.push("no plausible record length divides this file exactly; "+
    "it may carry a header, a trailer, or variable-length records");
  return notes;
}

/* When a file neither divides nor chains, its first eight bytes usually say
   why: a sane descriptor word means the chain derailed further in, a wild one
   means this is not RECFM=VB, and readable text means the transfer converted it. */
function describeHead(bytes){
  if(bytes.length < 4) return "file is only "+bytes.length+" bytes";
  var hex = [], i;
  for(i = 0; i < Math.min(8, bytes.length); i++)
    hex.push(("0"+bytes[i].toString(16).toUpperCase()).slice(-2));
  var declared = (bytes[0] << 8) | bytes[1];
  var note = "first bytes "+hex.join(" ")+" — as a descriptor word that is length "+
             declared+", reserved "+hex[2]+hex[3];
  if(bytes[2] !== 0 || bytes[3] !== 0) note += " (reserved bytes are not zero)";
  return note;
}

function analyse(){
  var host=document.getElementById("result");
  host.hidden=false; host.textContent="";
  if(!cpyText){ msg("choose a copybook first",true); return; }
  var enc=document.getElementById("in-enc").value;
  var layout;
  try{ layout=COBOL.parse(cpyText); }
  catch(err){
    msg("",false);
    var e=el("div","find critical");
    var h=el("div","fhead");
    h.appendChild(el("span","chip critical","refused"));
    h.appendChild(el("span","fcode","COPYBOOK"));
    e.appendChild(h);
    e.appendChild(el("p","fclaim",err.message));
    host.appendChild(e); return;
  }

  var head=el("div");
  head.appendChild(el("div","eyebrow",cpyName+"  ·  "+layout.recordLen+"-byte record  ·  "+
    layout.fields.length+" fields"));
  host.appendChild(head);

  var wrap=el("div","scroller");
  var t=document.createElement("table"); t.className="layout";
  var hr=document.createElement("tr");
  ["off","len","name","picture","usage"].forEach(function(x){
    var th=document.createElement("th"); th.textContent=x; hr.appendChild(th);});
  t.appendChild(hr);
  layout.fields.forEach(function(f){
    var tr=document.createElement("tr");
    [[f.offset,"n"],[COBOL.size(f)*f.occurs,"n"],[f.name,""],
     [f.pic?f.pic.raw:"—",""],[f.usage,""]].forEach(function(c){
      var td=document.createElement("td"); td.className=c[1];
      td.textContent=c[0]; tr.appendChild(td);});
    t.appendChild(tr);});
  wrap.appendChild(t); host.appendChild(wrap);

  if(!datBytes){
    msg("Layout parsed. Add a data file to measure it against.");
    return;
  }

  var n=datBytes.length/layout.recordLen;
  if(datBytes.length % layout.recordLen !== 0){
    var d=el("div","find critical"); var fh=el("div","fhead");
    fh.appendChild(el("span","chip critical","critical"));
    fh.appendChild(el("span","fcode","LAYOUT_MISMATCH"));
    d.appendChild(fh);
    d.appendChild(el("p","fclaim","the file is not a whole multiple of this copybook's "+
      "record length, so this copybook does not describe this file. Nothing else would "+
      "be trustworthy, so the scan stops here."));
    d.appendChild(el("div","fev","file_bytes="+datBytes.length.toLocaleString()+
      "   record_length="+layout.recordLen+
      "   remainder="+(datBytes.length%layout.recordLen)));
    explainMismatch(datBytes.length, layout.recordLen).forEach(function(note){
      d.appendChild(el("div","fev","— "+note));
    });
    d.appendChild(el("div","fev","— "+describeHead(datBytes)));
    host.appendChild(d);
    msg("");
    return;
  }

  var limited=Math.min(n,MAX_RECORDS);
  var res=SCAN.scan(layout,datBytes,MAX_RECORDS);
  var found=SCAN.findings(layout,res);

  host.appendChild(el("div","eyebrow",datName+"  ·  "+n.toLocaleString()+" records"+
    (res.recfm==="vb" ? "  ·  RECFM=VB, descriptor words stripped" : "")+
    (limited<n ? "  ·  first "+limited.toLocaleString()+" examined" : "")+"  ·  "+enc));

  if(!found.length){
    host.appendChild(el("p","rmsg","No findings. Every field decodes cleanly and "+
      "nothing in the data contradicts the copybook."));
  }
  found.forEach(function(f){
    var d=el("div","find "+f.severity), h2=el("div","fhead");
    h2.appendChild(el("span","chip "+f.severity,f.severity));
    h2.appendChild(el("span","fcode",f.code));
    h2.appendChild(el("span","ffield",f.field));
    d.appendChild(h2);
    d.appendChild(el("p","fclaim",f.claim));
    var ev=Object.keys(f.evidence).map(function(k){return k+"="+f.evidence[k];}).join("   ");
    if(ev) d.appendChild(el("div","fev",ev+"   ·   over "+f.records.toLocaleString()+" records"));
    if(f.impact) d.appendChild(el("div","fimp",f.impact));
    host.appendChild(d);
  });
  var crit=found.filter(function(f){return f.severity==="critical";}).length;
  msg(found.length+" finding(s)"+(crit?", "+crit+" critical":""));
}
})();
