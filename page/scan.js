/* The same finding rules as src/overpunch/findings.py, running in the browser.

   Kept as an independent implementation on purpose. page/verify_js.js runs both
   over the same files and requires the finding sets to match; two implementations
   disagreeing is the cheapest bug detector this project has. */
var SCAN = (function(){
"use strict";

var CP037 = null;                       // injected: 256-char decode table
var POS = "{ABCDEFGHI", NEG = "}JKLMNOPQR";

function setTable(t){ CP037 = t; }

function text(bytes, off, len){
  var o = "";
  for(var i = off; i < off + len; i++) o += CP037[bytes[i]];
  return o;
}
function splitSign(t){
  if(!t.length) return {d:"", s:1};
  var last = t.charAt(t.length-1), i;
  if(last === "-" || last === "+") return {d:t.slice(0,-1), s:last==="-"?-1:1};
  i = NEG.indexOf(last); if(i>=0) return {d:t.slice(0,-1)+i, s:-1};
  i = POS.indexOf(last); if(i>=0) return {d:t.slice(0,-1)+i, s:1};
  return {d:t, s:1};
}
function digitsOf(s){ return (s.replace(/[^0-9]/g,"") || "0"); }

/* The sign is in the ZONE NIBBLE of the last byte - 0xC_ positive, 0xD_
   negative - which every EBCDIC page shares. The character it decodes to does
   not: 0xD0 is '}' in cp037, 'ü' in cp273, 'ğ' in cp1026. Reading the sign from
   text works on US data and silently loses German negatives. */
/* COMP-3: two digits per byte, sign in the low nibble of the last one. The
   scanner used to check only that the nibbles were valid and never read the
   value, so scaled packed money reported a total of zero. */
function decodePacked(bytes, off, len, scale){
  var digits = "", sign = 1;
  for(var i = 0; i < len; i++){
    var b = bytes[off + i], hi = b >> 4, lo = b & 15;
    digits += String(hi);
    if(i === len - 1) sign = (lo === 0x0B || lo === 0x0D) ? -1 : 1;
    else digits += String(lo);
  }
  var v = parseInt(digits || "0", 10) * sign;
  return scale ? v / Math.pow(10, scale) : v;
}

function zoneSign(byte){
  var zone = byte >> 4, digit = byte & 15;
  if((zone === 0x0C || zone === 0x0D) && digit <= 9)
    return {s: zone === 0x0D ? -1 : 1, d: String(digit)};
  return null;
}

function fieldLen(f){ return COBOL.size(f) * f.occurs; }

/* IBM hexadecimal float: sign bit, 7-bit excess-64 exponent, base-SIXTEEN
   fraction. Not IEEE 754 - unpacking these bytes as a C float returns a
   plausible wrong number. */
function decodeHexFloat(bytes, off, len){
  var head = bytes[off], sign = (head & 0x80) ? -1 : 1, exp = (head & 0x7F) - 64;
  var frac = 0;
  for(var i = 1; i < len; i++) frac = frac * 256 + bytes[off + i];
  if(frac === 0) return 0;
  return sign * (frac / Math.pow(2, 8 * (len - 1))) * Math.pow(16, exp);
}

function observe(st, f, bytes, off){
  st.examined++;
  var len = fieldLen(f);
  if(f.usage === "COMP-1" || f.usage === "COMP-2"){
    st.sumCorrect += decodeHexFloat(bytes, off + f.offset, len);
    return;
  }
  var t = text(bytes, off + f.offset, len);
  if(!f.pic) return;
  if(t.indexOf("\uFFFD") >= 0) st.undecodable++;   // replacement char, escaped

  if(!f.pic || !f.pic.numeric){
    st.distinct[t] = (st.distinct[t]||0) + 1;
    if(!t.trim()) st.blank++;
    return;
  }
  if(f.usage === "COMP-3"){
    var bad = false;
    for(var i = off+f.offset; i < off+f.offset+len-1; i++){
      if((bytes[i]>>4) > 9 || (bytes[i]&15) > 9) bad = true;
    }
    var lo = bytes[off+f.offset+len-1] & 15;
    if(lo !== 12 && lo !== 13 && lo !== 15) bad = true;
    if(bad) st.invalidPacked++;
    else st.sumCorrect += decodePacked(bytes, off + f.offset, len, f.pic.scale || 0);
    return;
  }
  if(f.usage === "COMP"){
    var v = 0;
    for(var k = 0; k < len; k++) v = v * 256 + bytes[off + f.offset + k];
    if(f.pic.signed && (bytes[off + f.offset] & 0x80))
      v -= Math.pow(2, 8 * len);
    st.sumCorrect += f.pic.scale ? v / Math.pow(10, f.pic.scale) : v;
    return;
  }
  if(f.usage !== "DISPLAY") return;

  var zoned = zoneSign(bytes[off + f.offset + len - 1]);
  var last = t.length ? t.charAt(t.length-1) : "";
  if(zoned){
    st.nonDigitLast++;
    if(zoned.s < 0) st.negative++; else st.positive++;
  } else if(last && !/[0-9]/.test(last)){
    st.nonDigitLast++;
    var sp = splitSign(t);
    if(sp.s < 0) st.negative++; else st.positive++;
  }
  var sp2 = zoned ? {d: t.slice(0,-1) + zoned.d, s: zoned.s} : splitSign(t);
  var d = digitsOf(sp2.d);
  if(!t.trim()) st.blank++;
  if(d && /^0+$/.test(d)) st.allZero++;
  st.widest = Math.max(st.widest, d.replace(/^0+/,"").length);

  var v = parseInt(digitsOf(sp2.d),10);
  var div = Math.pow(10, f.pic.scale||0);
  var signed = (f.pic.signed ? sp2.s : 1) * v / div;
  st.sumCorrect += signed;
  st.sumAbs += Math.abs(signed);
  st.sumNaive += parseInt(digitsOf(t),10);
  if(!f.pic.signed) st.sumForced += sp2.s * v / div;
}

/* RECFM=VB: each record carries a 4-byte descriptor word - a big-endian length
   that INCLUDES the RDW, then two reserved zero bytes. Divisibility alone is not
   the test; the descriptor has to be real. */
function recordOffsets(bytes, recordLen){
  if(recordLen > 0 && bytes.length % recordLen === 0){
    var offs = [];
    for(var i = 0; i + recordLen <= bytes.length; i += recordLen) offs.push(i);
    return {recfm: "fixed", offsets: offs};
  }
  // Walk the descriptor chain and require it to land exactly on the end of the
  // file. A first descriptor that looks plausible proves nothing; a chain that
  // consumes the file to the byte is not a coincidence. Reserved bytes are
  // checked strictly first, then leniently - a dataset moved off a mainframe
  // does not always keep them.
  for(var strict = 1; strict >= 0; strict--){
    var o = [], at = 0, ok = true;
    while(at + 4 <= bytes.length){
      var len = (bytes[at] << 8) | bytes[at+1];
      if(len < 4 || at + len > bytes.length){ ok = false; break; }
      if(strict && (bytes[at+2] !== 0 || bytes[at+3] !== 0)){ ok = false; break; }
      o.push(at + 4);
      at += len;
    }
    if(ok && at === bytes.length && o.length) return {recfm: "vb", offsets: o};
  }
  return {recfm: "mismatch", offsets: []};
}

function scan(layout, bytes, limit){
  var stats = {}, found = recordOffsets(bytes, layout.recordLen);
  var offsets = found.offsets;
  if(limit) offsets = offsets.slice(0, limit);
  var n = offsets.length;
  layout.fields.forEach(function(f){
    stats[f.name] = {examined:0, blank:0, allZero:0, nonDigitLast:0, negative:0,
                     positive:0, widest:0, distinct:{}, undecodable:0,
                     invalidPacked:0, sumCorrect:0, sumAbs:0, sumNaive:0, sumForced:0};
  });
  for(var r = 0; r < n; r++){
    var off = offsets[r];
    layout.fields.forEach(function(f){ observe(stats[f.name], f, bytes, off); });
  }
  return {stats:stats, records:n, recfm:found.recfm};
}

function money(x){ return x.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2}); }
function pct(a,b){ return b ? (100*a/b).toFixed(1)+"%" : "n/a"; }

function findings(layout, res){
  var out = [];
  layout.fields.forEach(function(f){
    var st = res.stats[f.name];
    if(!st || !st.examined) return;
    var p = f.pic, isFiller = f.name.toUpperCase() === "FILLER";
    var signedBytes = p && p.numeric && f.usage === "DISPLAY" && (st.negative + st.positive);

    if(signedBytes){
      if(p.signed && st.negative){
        out.push({code:"TRAILING_SIGN", severity:"critical", field:f.name,
          claim:"the last byte carries the sign, not a digit; dropping it inverts the negative records",
          evidence:{negative:st.negative.toLocaleString(), positive:st.positive.toLocaleString(),
                    share_negative:pct(st.negative, st.examined)},
          records:st.examined,
          impact:"correct total "+money(st.sumCorrect)+"; sign ignored "+money(st.sumAbs)+
                 " (overstated by "+money(st.sumAbs-st.sumCorrect)+")"});
      } else if(p.signed){
        out.push({code:"SIGN_PRESENT_ALL_POSITIVE", severity:"info", field:f.name,
          claim:"the final byte is a sign, not a digit, but every record in this file is positive; a digits-only read happens to agree here and will stop agreeing the first time a credit appears",
          evidence:{positive:st.positive.toLocaleString(), negative:"0"}, records:st.examined});
      } else {
        out.push({code:"UNDECLARED_SIGN", severity:"critical", field:f.name,
          claim:"copybook declares this field unsigned (PIC 9) but the bytes carry a sign in the final position; the copybook is wrong about the data",
          evidence:{signed_records:signedBytes.toLocaleString(), share:pct(signedBytes, st.examined),
                    declared:p.raw}, records:st.examined,
          impact:"read as declared "+money(st.sumCorrect)+"; honouring the sign "+
                 money(st.sumForced)+" (difference "+money(st.sumCorrect-st.sumForced)+")"});
      }
    }
    if(p && p.numeric && p.scale){
      var display = f.usage === "DISPLAY";
      out.push({code:"IMPLIED_DECIMAL", severity:"warn", field:f.name,
        claim: display
          ? p.scale+" implied decimal place(s); the bytes contain no decimal point, so a digits-only read is 10^"+p.scale+" too large"
          : p.scale+" implied decimal place(s) on a "+f.usage+" field; the value is stored unscaled, and a decoder that returns the integer leaves the caller 10^"+p.scale+" too large",
        evidence:{declared:p.raw, scale:p.scale, usage:f.usage},
        records:st.examined,
        impact: display
          ? "correct total "+money(st.sumCorrect)+"; digits-only read "+st.sumNaive.toLocaleString()
          : "correct total "+money(st.sumCorrect)+"; unscaled "+
            Math.round(st.sumCorrect*Math.pow(10,p.scale)).toLocaleString()});
    }
    if(p && p.numeric && p.scale === 0 && st.widest && st.widest <= (p.digits - p.scale) - 1){
      out.push({code:"WIDTH_UNDERFILL", severity:"warn", field:f.name,
        claim:"declared "+(p.digits-p.scale)+" integer digits but no record uses more than "+st.widest+"; the source may be narrower than the copybook, or values may be truncated",
        evidence:{declared_digits:p.digits-p.scale, widest_observed:st.widest},
        records:st.examined});
    }
    if(isFiller && st.blank < st.examined){
      out.push({code:"POPULATED_FILLER", severity:"warn",
        field:"FILLER @ offset "+f.offset,
        claim:"declared FILLER but carries data; something is in this field that the copybook does not describe",
        evidence:{non_blank:(st.examined-st.blank).toLocaleString(),
                  distinct_values:Object.keys(st.distinct).length}, records:st.examined});
    }
    if(st.blank === st.examined || st.allZero === st.examined){
      out.push({code:"NEVER_POPULATED", severity:"info", field:f.name,
        claim:"field is blank or zero in every record examined",
        evidence:{blank:st.blank.toLocaleString(), zero:st.allZero.toLocaleString()},
        records:st.examined});
    }
    if(st.invalidPacked){
      out.push({code:"INVALID_PACKED", severity:"critical", field:f.name,
        claim:"declared COMP-3 but the nibbles are not valid packed decimal; the usage or the offset is wrong",
        evidence:{bad_records:st.invalidPacked.toLocaleString(),
                  share:pct(st.invalidPacked, st.examined)}, records:st.examined});
    }
    if(st.undecodable && st.undecodable > st.examined * 0.01){
      out.push({code:"ENCODING_SUSPECT", severity:"warn", field:f.name,
        claim:"bytes do not decode cleanly in the chosen code page",
        evidence:{undecodable:st.undecodable.toLocaleString(),
                  share:pct(st.undecodable, st.examined)}, records:st.examined});
    }
  });

  layout.fields.forEach(function(f){
    var keys = Object.keys(f.conditions);
    if(!keys.length) return;
    var st = res.stats[f.name], claimed = {}, owner = {};
    keys.forEach(function(k){ f.conditions[k].forEach(function(v){
      claimed[v] = (claimed[v]||0)+1; (owner[v]=owner[v]||[]).push(k); }); });
    Object.keys(claimed).forEach(function(v){
      if(claimed[v] > 1) out.push({code:"AMBIGUOUS_CONDITION", severity:"critical",
        field:f.name,
        claim:"value '"+v+"' is claimed by "+claimed[v]+" different condition names; which one is meant cannot be settled from the copybook or the data",
        evidence:{value:v, names:owner[v].join(", ")}, records:st?st.examined:0});
    });
    if(st){
      var extra = {};
      Object.keys(st.distinct).forEach(function(v){
        var t = v.trim(); if(t && !(t in claimed)) extra[t] = st.distinct[v]; });
      var names = Object.keys(extra);
      if(names.length) out.push({code:"UNCOVERED_VALUE", severity:"warn", field:f.name,
        claim:"values appear in the data that no 88-level accounts for; code that switches on the declared conditions will fall through for these records",
        evidence:{values:names.slice(0,5).map(function(v){return "'"+v+"' x"+extra[v];}).join(", "),
                  records_affected:names.reduce(function(a,v){return a+extra[v];},0).toLocaleString()},
        records:st.examined});
    }
  });

  var order = {critical:0, warn:1, info:2};
  out.sort(function(a,b){
    return (order[a.severity]-order[b.severity]) || a.field.localeCompare(b.field); });
  return out;
}

return {setTable:setTable, scan:scan, findings:findings, splitSign:splitSign,
        text:text, recordOffsets:recordOffsets};
})();
