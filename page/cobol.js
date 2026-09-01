/* A COBOL copybook parser in the browser.

   Written so the page can analyse YOUR files without them going anywhere. The
   data this tool exists for - benefit records, tax extracts, card transactions -
   is exactly the data nobody may upload to a website, so the analysis has to
   come to the bytes rather than the other way round. Nothing here touches the
   network.

   Deliberately a second implementation of src/overpunch/copybook.py rather than
   a port of it: page/verify_js.js compares the two across every copybook in the
   repository, and disagreement is how the Python decoder's digit-dropping bug
   was found in the first place. */
var COBOL = (function(){
"use strict";

var USAGES = {
  "COMP":"COMP","COMPUTATIONAL":"COMP","COMP-4":"COMP","COMPUTATIONAL-4":"COMP",
  "BINARY":"COMP","COMP-3":"COMP-3","COMPUTATIONAL-3":"COMP-3",
  "PACKED-DECIMAL":"COMP-3","COMP-1":"COMP-1","COMPUTATIONAL-1":"COMP-1",
  "COMP-2":"COMP-2","COMPUTATIONAL-2":"COMP-2"
};

// Compiler-directing statements: they control the listing, carry no period, and
// left in place they merge with the next line and swallow the field after it.
var DIRECTIVES = {SKIP1:1, SKIP2:1, SKIP3:1, EJECT:1, TITLE:1};

/* The sequence area is columns 1-6 whatever it contains: `000100` and `00001 `
   are both ordinary. Insisting on six digits made every comment parse as a
   statement and produced a zero-byte record with no error. A level number
   cannot be four to six leading digits, so this cannot misfire on free format. */
function detectFormat(text){
  var sequenced = 0, meaningful = 0;
  text.split(/\r?\n/).forEach(function(raw){
    var line = raw.replace(/[\r\n]+$/,"");
    if(!line.trim()) return;
    meaningful++;
    if(/^\d{4,6}[\s\-*\/]/.test(line)) sequenced++;
  });
  if(!meaningful) return "free";
  return sequenced >= meaningful * 0.5 ? "fixed" : "free";
}

function stripLine(line, fmt){
  line = line.replace(/[\r\n]+$/,"");
  if(fmt === "fixed"){
    if(line.length > 72) line = line.slice(0,72);
    if(line.length > 6 && (line.charAt(6) === "*" || line.charAt(6) === "/")) return "";
    if(line.replace(/^\s+/,"").charAt(0) === "*") return "";
    if(line.length <= 6) return "";
    line = "      " + line.slice(6);
  } else if(line.replace(/^\s+/,"").charAt(0) === "*") return "";
  var body = line.trim();
  if(body){
    var first = body.split(/\s+/)[0].replace(/\.$/,"").toUpperCase();
    if(DIRECTIVES[first]) return "";
  }
  return line;
}

/* A period ends a statement only when a space or the line end follows it.
   Splitting on every period breaks `VALUE -9999999.99.` at the decimal point. */
function terminator(text){
  var quote = null;
  for(var i = 0; i < text.length; i++){
    var ch = text.charAt(i);
    if(quote){ if(ch === quote) quote = null; continue; }
    if(ch === "'" || ch === '"') quote = ch;
    else if(ch === "." && (i + 1 === text.length || /\s/.test(text.charAt(i+1))))
      return i;
  }
  return -1;
}

function checkTruncation(text){
  var lines = text.split(/\r?\n/);
  for(var i=0;i<lines.length;i++){
    var l = lines[i].replace(/[\r\n]+$/,"");
    if(l.length <= 72 || !/^\d{6}/.test(l)) continue;
    var kept = l.slice(6,72), tail = l.slice(72);
    if(tail.indexOf(".") >= 0 && kept.indexOf(".") < 0)
      throw new Error("line "+(i+1)+" runs past column 72 and its terminating "+
        "period falls in the discarded identification area, so the statement "+
        "never ends and merges with the next one. Shorten the line.");
  }
}

function statements(text, fmt){
  fmt = fmt || detectFormat(text);
  var out=[], buf=[];
  text.split(/\r?\n/).forEach(function(raw){
    var line = stripLine(raw, fmt);
    if(!line.trim()) return;
    buf.push(line.trim());
    var joined = buf.join(" ");
    for(;;){
      var cut = terminator(joined);
      if(cut < 0) break;
      var head = joined.slice(0,cut);
      joined = joined.slice(cut+1);
      if(head.trim()) out.push(head.trim());
    }
    buf = joined.trim() ? [joined] : [];
  });
  if(buf.length && buf.join(" ").trim()) out.push(buf.join(" ").trim());
  return out;
}

function parsePicture(pic){
  var src = pic.toUpperCase().replace(/\s+/g,"");
  var signed = src.charAt(0) === "S";
  if(signed) src = src.slice(1);
  var expanded="", i=0;
  while(i < src.length){
    var ch = src.charAt(i);
    var m = /^\((\d+)\)/.exec(src.slice(i+1));
    if(m){ expanded += new Array(parseInt(m[1],10)+1).join(ch); i += 1 + m[0].length; }
    else { expanded += ch; i += 1; }
  }
  function count(s,c){ return (s.match(new RegExp(c,"g"))||[]).length; }
  if(expanded.indexOf("9") >= 0){
    var v = expanded.indexOf("V");
    var before = v<0 ? expanded : expanded.slice(0,v);
    var after  = v<0 ? ""       : expanded.slice(v+1);
    return {raw:pic, numeric:true, digits:count(before,"9")+count(after,"9"),
            scale:v<0?0:count(after,"9"), signed:signed, chars:0};
  }
  return {raw:pic, numeric:false, digits:0, scale:0, signed:false,
          chars:(expanded.match(/[XA9]/g)||[]).length};
}

function tokens(stmt){ return stmt.match(/'[^']*'|"[^"]*"|\S+/g) || []; }

function elementarySize(f){
  if(f.usage === "COMP-1") return 4;
  if(f.usage === "COMP-2") return 8;
  var p = f.pic;
  if(!p) return 0;
  if(!p.numeric) return p.chars;
  if(f.usage === "COMP-3") return Math.floor(p.digits/2) + 1;
  if(f.usage === "COMP") return p.digits<=4 ? 2 : (p.digits<=9 ? 4 : 8);
  return p.digits + ((p.signed && f.signSeparate) ? 1 : 0);
}

function size(f){
  if(!f.children.length) return elementarySize(f);
  var total = 0;
  f.children.forEach(function(c){
    // a REDEFINES shares bytes only with a field that is HERE; one naming a
    // record from another copybook has nothing to share with, and IS the record
    if(c.redefines && !c.redefinesExternal) return;
    total += size(c) * c.occurs;
  });
  return total;
}

function parse(text){
  checkTruncation(text);
  var fmt = detectFormat(text), fragment = false;
  var root=null, stack=[], lastElem=null;
  statements(text, fmt).forEach(function(stmt){
    var tok = tokens(stmt);
    if(!tok.length || !/^\d+$/.test(tok[0])) return;
    var level = parseInt(tok[0],10), rest = tok.slice(1);

    if(level === 88){
      if(!lastElem) return;
      var nm = rest[0] || "FILLER";
      var vals = rest.slice(1).filter(function(t){
        return ["VALUE","VALUES","IS","ARE","THRU","THROUGH"].indexOf(t.toUpperCase())<0;
      }).map(function(t){ return t.replace(/^['"]|['"]$/g,""); });
      (lastElem.conditions[nm] = lastElem.conditions[nm] || []).push.apply(
        lastElem.conditions[nm], vals);
      return;
    }
    if(level === 66) return;

    var name="FILLER", idx=0;
    if(rest.length && ["PIC","PICTURE","REDEFINES","OCCURS"].indexOf(rest[0].toUpperCase())<0){
      name = rest[0]; idx = 1;
    }
    var f = {level:level, name:name, pic:null, usage:"DISPLAY", occurs:1,
             occursDependingOn:null, redefines:null, signSeparate:false,
             signLeading:false, conditions:{}, children:[], offset:0, parent:null};

    while(idx < rest.length){
      var w = rest[idx].toUpperCase();
      if(w === "PIC" || w === "PICTURE"){
        idx++; if(idx<rest.length && rest[idx].toUpperCase()==="IS") idx++;
        f.pic = parsePicture(rest[idx]);
      } else if(w === "REDEFINES"){ idx++; f.redefines = rest[idx]; }
      else if(w === "OCCURS"){
        idx++; f.occurs = parseInt(rest[idx],10);
        if(idx+2 < rest.length && rest[idx+1].toUpperCase()==="TO"){
          f.occurs = parseInt(rest[idx+2],10); idx += 2;
        }
        for(var j=idx;j<rest.length-1;j++){
          if(rest[j].toUpperCase()==="ON"){ f.occursDependingOn = rest[j+1]; break; }
        }
      }
      else if(w === "SIGN"){
        var tail = rest.slice(idx).map(function(t){return t.toUpperCase();});
        if(tail.indexOf("LEADING")>=0) f.signLeading = true;
        if(tail.indexOf("SEPARATE")>=0) f.signSeparate = true;
      }
      else if(w === "SEPARATE") f.signSeparate = true;
      else if(w === "LEADING") f.signLeading = true;
      else if(USAGES[w]) f.usage = USAGES[w];
      idx++;
    }

    if(root === null){
      if(level === 1){ root = f; stack = [f]; if(f.pic) lastElem = f; return; }
      // a fragment: fields meant to be COPY'd into a record declared elsewhere
      fragment = true;
      root = {level:0, name:"<fragment>", pic:null, usage:"DISPLAY", occurs:1,
              occursDependingOn:null, redefines:null, redefinesExternal:false,
              signSeparate:false, signLeading:false, conditions:{}, children:[],
              offset:0, parent:null};
      stack = [root];
      root.children.push(f); stack.push(f);
      if(f.pic) lastElem = f;
      return;
    } else {
      while(stack.length && stack[stack.length-1].level >= level) stack.pop();
      if(!stack.length) throw new Error("orphaned level "+level+" field "+name);
      stack[stack.length-1].children.push(f);
      stack.push(f);
    }
    if(f.pic) lastElem = f;
  });
  if(root === null) throw new Error("no 01-level record found");

  function find(node, want){
    if(node.name.toUpperCase() === want.toUpperCase()) return node;
    for(var i=0;i<node.children.length;i++){
      var hit = find(node.children[i], want); if(hit) return hit;
    }
    return null;
  }
  (function markExternal(f){
    if(f.redefines) f.redefinesExternal = find(root, f.redefines) === null;
    f.children.forEach(markExternal);
  })(root);

  // lay out ONE occurrence and return where it ends; the caller multiplies.
  // Returning size*occurs here multiplied it twice for an elementary field with
  // OCCURS - PIC X(40) OCCURS 5 advanced 1000 bytes instead of 200.
  function walk(f, base){
    f.offset = base;
    if(!f.children.length) return base + size(f);
    var cursor = base;
    f.children.forEach(function(c){
      c.parent = f;
      if(c.redefines && !c.redefinesExternal){
        var t = find(root, c.redefines);
        walk(c, t ? t.offset : cursor);
        return;
      }
      var end = walk(c, cursor);
      cursor = cursor + (end - cursor) * c.occurs;
    });
    return cursor;
  }
  walk(root, 0);

  var leaves = [];
  (function rec(f){
    if(f.children.length) f.children.forEach(rec);
    else if(f.pic || f.usage === "COMP-1" || f.usage === "COMP-2") leaves.push(f);
  })(root);

  var recordLen = size(root);
  var reach = leaves.reduce(function(m,f){
    return Math.max(m, f.offset + size(f)*f.occurs); }, 0);
  if(reach > recordLen)
    throw new Error("field offsets reach byte "+reach+" but the record is "+
                    recordLen+" bytes; the layout disagrees with itself");

  return {root:root, recordLen:recordLen, fields:leaves, isFragment:fragment,
          find:function(n){return find(root,n);}, size:size};
}

return {parse: parse, parsePicture: parsePicture, statements: statements,
        elementarySize: elementarySize, size: size,
        detectFormat: detectFormat, terminator: terminator};
})();
