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
      "   remainder="+(datBytes.length%layout.recordLen)+
      "   ·   a variable-length (RECFM=VB) file will always look like this"));
    host.appendChild(d);
    msg("");
    return;
  }

  SCAN.setTable(TABLES[enc]);
  var limited=Math.min(n,MAX_RECORDS);
  var res=SCAN.scan(layout,datBytes,MAX_RECORDS);
  var found=SCAN.findings(layout,res);

  host.appendChild(el("div","eyebrow",datName+"  ·  "+n.toLocaleString()+" records"+
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
