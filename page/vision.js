/* ---- copybook recovered from a scan ---- */
(function(){
if(!D.vision || !D.vision.reads) return;
var V=D.vision, idx=0;
document.getElementById("scanimg").src="data:image/png;base64,"+V.image;
document.getElementById("visionmodel").textContent=V.model;
function render(){
  var r=V.reads[idx];
  document.getElementById("visionout").textContent=r.copybook;
  var host=document.getElementById("visionchecks"); host.textContent="";
  var all=true;
  r.checks.forEach(function(c){
    if(!c[1]) all=false;
    var d=el("div","vcheck "+(c[1]?"ok":"no"));
    d.appendChild(el("span","vm",c[1]?"PASS":"FAIL"));
    d.appendChild(el("span","vn",c[0]));
    d.appendChild(el("span","vd",c[2]));
    host.appendChild(d);
  });
  host.appendChild(el("div","vverdict "+(all?"ok":"no"), all
    ? "Proved. This layout can be trusted against this file."
    : "Rejected. Plausible, parseable, and wrong — so it is not used."));
}
Array.prototype.forEach.call(document.querySelectorAll("[data-read]"),function(b){
  b.onclick=function(){
    Array.prototype.forEach.call(document.querySelectorAll("[data-read]"),function(o){
      o.setAttribute("aria-pressed",o===b?"true":"false");});
    idx=parseInt(b.getAttribute("data-read"),10); render();};});
render();
})();
