/* The same finding rules as src/overpunch/findings.py, running in the browser.

   Kept as an independent implementation on purpose. page/verify_js.js runs both
   over the same files and requires the finding sets to match; two implementations
   disagreeing is the cheapest bug detector this project has. */
var SCAN = (function(){
"use strict";

var CP037 = null;                       // injected: 256-char decode table
var POS = "{ABCDEFGHI", NEG = "}JKLMNOPQR";

/* Only these can be a sign. A low-value byte, a space or any other junk in the
   final position means the field is UNSET, not negative. Treating every
   non-digit as a sign reported each empty one-digit indicator as a copybook
   error - and this was fixed in the Python and not here, so the two disagreed
   on a real file until a reference case was added that ends a numeric field in
   a low-value byte. */
var SIGN_CHARS = {};
"{ABCDEFGHI}JKLMNOPQR+-".split("").forEach(function(c){ SIGN_CHARS[c] = true; });

/* ...and these are how a mainframe says "no value". Trailing spaces are
   ordinary in a numeric field, so the unknown-byte rule has to exempt them. */
var PADDING = {"\u0000": true, " ": true, "\u00ff": true};

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
/* Two of these, deliberately. Arithmetic needs a "0" when a field holds no
   digits at all; the STATISTICS must not, because a field of undecodable bytes
   is unreadable, not zero. Sharing the fallback made every record of an
   ASCII-native file read as EBCDIC count as all-zero, and the browser reported
   NEVER_POPULATED on a column that is populated in all 60 records while the
   Python said nothing. Found by adding that file to the cross-check corpus. */
function digitsRaw(s){ return s.replace(/[^0-9]/g,""); }
function digitsOf(s){ return digitsRaw(s) || "0"; }

/* Low-values and spaces are how a mainframe says "no value". str.trim() does
   not remove 0x00, so a column left entirely unset looked populated. */
function isUnset(t){
  if(!t.length) return true;
  for(var i = 0; i < t.length; i++){
    var c = t.charAt(i);
    if(c !== "\u0000" && c !== " " && c !== "\u00ff") return false;
  }
  return true;
}

/* The sign is in the ZONE NIBBLE of the last byte - 0xC_ positive, 0xD_
   negative - which every EBCDIC page shares. The character it decodes to does
   not: 0xD0 is '}' in cp037, 'ü' in cp273, 'ğ' in cp1026. Reading the sign from
   text works on US data and silently loses German negatives. */
/* COMP-3: two digits per byte, sign in the low nibble of the last one. The
   scanner used to check only that the nibbles were valid and never read the
   value, so scaled packed money reported a total of zero. */
/* Returns null when the bytes are not packed decimal. String(hi) on a nibble
   above 9 emits "10".."15" straight into the digit string, so three junk bytes
   produced 1,515,151,515 - a confident number out of bytes that are not a
   number. The scanner validates before calling this; the rendered table did
   not, so the page showed it. */
function decodePacked(bytes, off, len, scale){
  var digits = "", sign = 1;
  for(var i = 0; i < len; i++){
    var b = bytes[off + i], hi = b >> 4, lo = b & 15;
    if(hi > 9) return null;
    digits += String(hi);
    if(i === len - 1){
      if(lo < 0x0A) return null;             // a digit where the sign must be
      sign = (lo === 0x0B || lo === 0x0D) ? -1 : 1;
    } else {
      if(lo > 9) return null;
      digits += String(lo);
    }
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

/* Micro Focus, ACUCOBOL and the other PC COBOLs never translate from EBCDIC:
   they write ASCII digits and fold the sign in by setting 0x40 in the zone, so
   -123.45 ends in 0x75 ('u'). That is a THIRD convention, not a variant of the
   mainframe's 0xD5 nor of the '}' a translated file leaves behind, and only the
   code page says which is in play - hence isAsciiPage rather than trying it
   opportunistically, which on EBCDIC data would invent negatives.

   The positive form is a plain digit and so indistinguishable from an unsigned
   field: it is used for the VALUE, but only the negative form is counted as a
   sign byte, or every ASCII numeric column would report as signed. */
function isAsciiPage(){ return CP037 && CP037.charAt(0x30) === "0"; }

/* Four or eight bytes of floating point are either IBM hexadecimal float or
   IEEE 754, and nothing in the bytes says which. The wrong reading does not
   fail - 161,916.39 written as IEEE and read as hex float comes back as
   505,354,496.00 - so the column has to settle it.

   IBM HFP keeps its fraction normalised, so the leading hex digit (high nibble
   of byte 1) is never zero for a non-zero value. In IEEE that nibble is the
   bottom of the exponent and the top of the mantissa, and it is zero 4.35% of
   the time for binary32 and 26.15% for binary64. One such value refutes hex
   float outright.

   The absence of one only means something if it COULD have appeared: a column
   whose values all sit inside one binade shares a single exponent byte, and
   then neither format produces one. Hence the spread gate. */
var FLOAT_SAMPLE_FLOOR = 67, FLOAT_EXPONENT_SPREAD = 2, FLOAT_BYTEORDER_RATIO = 2,
    BYTEORDER_SPREAD = 8;

/* Which end holds the high-order bytes, from how much each end varies. The
   high-order end of a real column takes FEW distinct byte values because
   magnitudes cluster; the low-order end takes many. The wide end has to
   actually be wide, or four records can "prove" anything. */
function byteorderFromEntropy(head, tail){
  if(Math.max(head, tail) < BYTEORDER_SPREAD) return null;
  if(tail * FLOAT_BYTEORDER_RATIO <= head) return "little";
  if(head * FLOAT_BYTEORDER_RATIO <= tail) return "big";
  return null;
}

/* Byte order is a SECOND unknown. In IEEE the sign and exponent sit at one end
   and the low mantissa bits at the other; business magnitudes cluster, so the
   exponent end takes few distinct byte values and the mantissa end takes many.
   Measured on a file GnuCOBOL 3.1.2 actually produced on x86: first byte 143
   distinct, last byte 3. Getting the format right and the order wrong returns
   1.16e-53 where the value is 1.727, with no complaint. */
function floatByteorder(st){
  return byteorderFromEntropy(Object.keys(st.floatHeadBytes).length,
                              Object.keys(st.floatTailBytes).length);
}
function floatReading(st){
  if(st.floatFormat === "ieee-le") return {fmt:"ieee", order:"little"};
  if(st.floatFormat === "ieee")    return {fmt:"ieee", order:"big"};
  return {fmt:"hfp", order:null};
}

function isUnnormalisedHfp(bytes, at, len){
  if(len < 2) return false;
  var allZero = true;
  for(var i = 0; i < len; i++) if(bytes[at+i]){ allZero = false; break; }
  if(allZero) return false;
  return (bytes[at+1] >> 4) === 0;
}

function floatEvidence(st){
  if(!st.floatNonzero) return null;
  if(st.floatUnnormalised) return "ieee";
  if(st.floatNonzero >= FLOAT_SAMPLE_FLOOR &&
     Object.keys(st.floatExponents).length >= FLOAT_EXPONENT_SPREAD) return "hfp";
  return null;
}

function asciiZoneSign(byte){
  if(byte >= 0x70 && byte <= 0x79) return {s: -1, d: String(byte - 0x70)};
  if(byte >= 0x30 && byte <= 0x39) return {s:  1, d: String(byte - 0x30)};
  return null;
}

function fieldLen(f){ return COBOL.size(f) * f.occurs; }

function inAnyRange(value, f){
  var ranges = f.conditionRanges || {};
  return Object.keys(ranges).some(function(k){
    return ranges[k].some(function(span){
      if(span[0] <= value && value <= span[1]) return true;
      var lo = parseFloat(span[0]), hi = parseFloat(span[1]), v = parseFloat(value);
      return !isNaN(lo) && !isNaN(hi) && !isNaN(v) && lo <= v && v <= hi;
    });
  });
}

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
    var fat = off + f.offset, anyByte = false;
    for(var q = 0; q < len; q++) if(bytes[fat+q]){ anyByte = true; break; }
    if(anyByte){
      st.floatNonzero++;
      st.floatExponents[bytes[fat] & 0x7F] = true;
      st.floatHeadBytes[bytes[fat]] = true;
      st.floatTailBytes[bytes[fat + len - 1]] = true;
      if(isUnnormalisedHfp(bytes, fat, len)) st.floatUnnormalised++;
    }
    st.sumCorrect += decodeHexFloat(bytes, off + f.offset, len);
    return;
  }
  var t = text(bytes, off + f.offset, len);
  if(!f.pic) return;
  if(t.indexOf("\uFFFD") >= 0) st.undecodable++;   // replacement char, escaped

  if(!f.pic || !f.pic.numeric){
    st.distinct[t] = (st.distinct[t]||0) + 1;
    if(!t.trim()) st.blank++;
    if(isUnset(t)) st.unset++;
    return;
  }
  // a numeric field can carry 88-levels too; without its values the
  // uncovered-value rule could never fire for one
  var coded = Object.keys(f.conditions || {}).length ||
              Object.keys(f.conditionRanges || {}).length;
  if(coded && Object.keys(st.distinct).length < 1000){
    var key = t.replace(/^\s+|\s+$/g, "");
    st.distinct[key] = (st.distinct[key] || 0) + 1;
  }

  if(f.usage === "COMP-3"){
    var bad = false;
    for(var i = off+f.offset; i < off+f.offset+len-1; i++){
      if((bytes[i]>>4) > 9 || (bytes[i]&15) > 9) bad = true;
    }
    var lo = bytes[off+f.offset+len-1] & 15;
    if(lo !== 12 && lo !== 13 && lo !== 15) bad = true;
    var packedValue = bad ? null
                          : decodePacked(bytes, off + f.offset, len, f.pic.scale || 0);
    if(packedValue === null) st.invalidPacked++;
    else st.sumCorrect += packedValue;
    return;
  }
  if(f.usage === "COMP" || f.usage === "COMP-5"){
    var bat = off + f.offset, anyBin = false;
    for(var z = 0; z < len; z++) if(bytes[bat+z]){ anyBin = true; break; }
    if(anyBin){
      st.binaryNonzero++;
      st.binaryHeadBytes[bytes[bat]] = true;
      st.binaryTailBytes[bytes[bat + len - 1]] = true;
    }
    var v = 0;
    for(var k = 0; k < len; k++) v = v * 256 + bytes[bat + k];
    if(f.pic.signed && (bytes[bat] & 0x80)) v -= Math.pow(2, 8 * len);
    st.binaryMaxAbs = Math.max(st.binaryMaxAbs, Math.abs(v));
    // standard COMP truncates to the PICTURE; COMP-5 does not
    if(Math.abs(v) > Math.pow(10, f.pic.digits) - 1) st.binaryOverPic++;
    st.sumCorrect += f.pic.scale ? v / Math.pow(10, f.pic.scale) : v;
    return;
  }
  if(f.usage !== "DISPLAY") return;

  var lastByte = bytes[off + f.offset + len - 1];
  var zoned = zoneSign(lastByte);
  if(!zoned && isAsciiPage()){
    var az = asciiZoneSign(lastByte);
    if(az && az.s < 0) zoned = az;
  }
  var last = t.length ? t.charAt(t.length-1) : "";
  if(zoned){
    st.nonDigitLast++;
    if(zoned.s < 0) st.negative++; else st.positive++;
  } else if(last && !/[0-9]/.test(last) && SIGN_CHARS[last]){
    st.nonDigitLast++;
    var sp = splitSign(t);
    if(sp.s < 0) st.negative++; else st.positive++;
  } else if(last && !/[0-9]/.test(last) && !PADDING[last]){
    /* neither a digit, nor any sign convention this decoder knows, nor the
       padding of an unset field. Reading it as digits drops the last one
       silently - which is how the ASCII-native convention went unnoticed. */
    st.unknownSign++;
    var hex = "0x" + (lastByte < 16 ? "0" : "") + lastByte.toString(16).toUpperCase();
    if(Object.keys(st.unknownSignBytes).length < 32)
      st.unknownSignBytes[hex] = (st.unknownSignBytes[hex] || 0) + 1;
  }
  var sp2 = zoned ? {d: t.slice(0,-1) + zoned.d, s: zoned.s} : splitSign(t);
  var d = digitsRaw(sp2.d);
  if(!t.trim()) st.blank++;
  if(isUnset(t)) st.unset++;
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
    stats[f.name] = {examined:0, blank:0, unset:0, allZero:0, nonDigitLast:0, negative:0,
                     positive:0, widest:0, distinct:{}, undecodable:0,
                     invalidPacked:0, unknownSign:0, unknownSignBytes:{},
                     floatNonzero:0, floatUnnormalised:0, floatExponents:{},
                     floatHeadBytes:{}, floatTailBytes:{}, floatFormat:"hfp",
                     binaryNonzero:0, binaryOverPic:0, binaryMaxAbs:0,
                     binaryHeadBytes:{}, binaryTailBytes:{}, binaryOrder:"big",
                     sumCorrect:0, sumAbs:0, sumNaive:0, sumForced:0};
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
    if(isFiller && st.unset < st.examined){
      out.push({code:"POPULATED_FILLER", severity:"warn",
        field:"FILLER @ offset "+f.offset,
        claim:"declared FILLER but carries data; something is in this field that the copybook does not describe",
        evidence:{non_blank:(st.examined-st.blank).toLocaleString(),
                  distinct_values:Object.keys(st.distinct).length}, records:st.examined});
    }
    if(st.blank === st.examined || st.allZero === st.examined ||
       st.unset === st.examined){
      out.push({code:"NEVER_POPULATED", severity:"info", field:f.name,
        claim:"field is blank, zero or low-values in every record examined",
        evidence:{blank:st.blank.toLocaleString(), zero:st.allZero.toLocaleString(),
                  unset:st.unset.toLocaleString(), of:st.examined.toLocaleString()},
        records:st.examined});
    }
    if((f.usage === "COMP" || f.usage === "COMP-5") && st.binaryNonzero && p){
      var bHead = Object.keys(st.binaryHeadBytes).length,
          bTail = Object.keys(st.binaryTailBytes).length,
          bOrder = byteorderFromEntropy(bHead, bTail),
          bWrong = bOrder && bOrder !== st.binaryOrder;
      if(bWrong){
        out.push({code:"BINARY_BYTE_ORDER", severity:"critical", field:f.name,
          claim:"this binary field is being read "+st.binaryOrder+"-endian and the bytes "+
                "say it is "+bOrder+"-endian; COMP-5 is defined as NATIVE order, and "+
                "nothing in the file records which machine's",
          evidence:{distinct_first_byte:String(bHead), distinct_last_byte:String(bTail),
                    reading_in_force:st.binaryOrder},
          records:st.examined,
          impact:"re-read with --binary-byteorder "+bOrder+". The high-order end of a "+
                 "real column varies little because magnitudes cluster; here it is the "+
                 (bOrder === "little" ? "last" : "first")+" byte. Read the wrong way "+
                 "round the values are still integers, just different ones"});
      } else if(f.usage === "COMP" && st.binaryOverPic){
        var lim = Math.pow(10, p.digits) - 1;
        out.push({code:"BINARY_EXCEEDS_PIC", severity:"critical", field:f.name,
          claim:"declared "+p.raw+" COMP, which a standard compiler truncates to "+
                lim.toLocaleString()+", but this field holds larger values; it is COMP-5 "+
                "or was compiled TRUNC(BIN), and the copybook does not say so",
          evidence:{over_limit:st.binaryOverPic.toLocaleString(),
                    share:pct(st.binaryOverPic, st.examined),
                    largest_seen:st.binaryMaxAbs.toLocaleString(),
                    pic_allows:lim.toLocaleString()},
          records:st.examined,
          impact:"one value above the limit is enough - a standard COMP field cannot "+
                 "contain one. Anything downstream that believes the PICTURE will size "+
                 "a column too small"});
      }
    }

    if(f.usage === "COMP-1" || f.usage === "COMP-2"){
      var ev = floatEvidence(st), spread = Object.keys(st.floatExponents).length,
          rd = floatReading(st), order = floatByteorder(st),
          heads = Object.keys(st.floatHeadBytes).length,
          tails = Object.keys(st.floatTailBytes).length,
          NAMES = {hfp:"IBM hexadecimal float", ieee:"big-endian IEEE 754",
                   "ieee-le":"little-endian IEEE 754"},
          contradicted = ev && (ev !== rd.fmt ||
                                (ev === "ieee" && order && order !== rd.order)),
          confirmed = ev === rd.fmt &&
                      (ev !== "ieee" || order === rd.order);
      if(contradicted){
        var want = ev === "hfp" ? "hfp" : (order === "little" ? "ieee-le" : "ieee");
        out.push({code:"FLOAT_FORMAT_MISMATCH", severity:"critical", field:f.name,
          claim:"this column is being read as "+NAMES[st.floatFormat]+", and the bytes "+
                "say it is "+NAMES[want]+"; nothing in a COMP-1/COMP-2 field records "+
                "which one wrote it - not the format and not the byte order - so both "+
                "are measured",
          evidence:{non_zero_values:st.floatNonzero.toLocaleString(),
                    unnormalisable_as_hex_float:st.floatUnnormalised.toLocaleString(),
                    share:pct(st.floatUnnormalised, st.floatNonzero),
                    distinct_first_byte:String(heads),
                    distinct_last_byte:String(tails),
                    reading_in_force:st.floatFormat},
          records:st.examined,
          impact:"re-read with --float-format "+want+". The wrong reading does not fail "+
                 "- it returns ordinary numbers, off by orders of magnitude. A real "+
                 "compiler proved this: GnuCOBOL on x86 wrote 1.72708 as 15 11 dd 3f, "+
                 "which read big-endian is 2.9457e-26"});
      } else if(confirmed){
        out.push({code:"FLOAT_FORMAT_CONFIRMED", severity:"info", field:f.name,
          claim:"read as "+st.floatFormat+", and the bytes support it - both the format "+
                "and the byte order were measured over the column, not assumed from a "+
                "default",
          evidence:{non_zero_values:st.floatNonzero.toLocaleString(),
                    unnormalisable_as_hex_float:st.floatUnnormalised.toLocaleString(),
                    distinct_magnitudes:String(spread),
                    distinct_first_byte:String(heads),
                    distinct_last_byte:String(tails)},
          records:st.examined});
      } else if(st.floatNonzero){
        out.push({code:"FLOAT_FORMAT_UNDECIDABLE", severity:"warn", field:f.name,
          claim:"this column cannot settle its floating-point format, or which end of it "+
                "holds the exponent, so the reading in force ("+st.floatFormat+") remains "+
                "a default, not a measurement",
          evidence:{non_zero_values:st.floatNonzero.toLocaleString(),
                    needed:FLOAT_SAMPLE_FLOOR.toLocaleString(),
                    distinct_magnitudes:String(spread),
                    needed_magnitudes:String(FLOAT_EXPONENT_SPREAD),
                    distinct_first_byte:String(heads),
                    distinct_last_byte:String(tails)},
          records:st.examined,
          impact:"a true IEEE column shows values that cannot be normalised hex float at "+
                 "4.35% (4-byte) or 26.15% (8-byte) - but only when the values span more "+
                 "than one magnitude. Too few of them, or all inside one binade, and "+
                 "NEITHER format produces one, so seeing none proves nothing"});
      }
    }

    if(p && p.numeric && f.usage === "DISPLAY" && st.unknownSign){
      var seen = Object.keys(st.unknownSignBytes).sort(function(a,b){
        return st.unknownSignBytes[b] - st.unknownSignBytes[a]; }).slice(0,4);
      out.push({code:"UNKNOWN_SIGN_BYTE", severity:"critical", field:f.name,
        claim:"the final byte is neither a digit, nor any sign convention this decoder implements, nor the padding of an unset field; reading it as digits silently drops the last one",
        evidence:{records:st.unknownSign.toLocaleString(),
                  share:pct(st.unknownSign, st.examined),
                  bytes:seen.map(function(b){
                    return b + " x" + st.unknownSignBytes[b].toLocaleString(); }).join(", ")},
        records:st.examined,
        impact:"the code page or the sign convention is wrong; an ASCII-native zoned file read as EBCDIC lands here"});
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
    var st = res.stats[f.name], claimed = {}, owner = {}, singles = {};
    keys.forEach(function(k){
      var vals = f.conditions[k] || [];
      vals.forEach(function(v){
        claimed[v] = (claimed[v]||0)+1; (owner[v]=owner[v]||[]).push(k); });
      // only a condition whose ONLY value is v makes v ambiguous; one that
      // enumerates the permitted set beside the specific ones is ordinary COBOL
      var ranges = (f.conditionRanges || {})[k];
      if(vals.length === 1 && !(ranges && ranges.length)){
        singles[vals[0]] = (singles[vals[0]]||0)+1;
        (owner[vals[0]]=owner[vals[0]]||[]);
      }
    });
    Object.keys(singles).forEach(function(v){
      if(singles[v] > 1) out.push({code:"AMBIGUOUS_CONDITION", severity:"critical",
        field:f.name,
        claim:"value '"+v+"' is claimed by "+singles[v]+" different condition names; which one is meant cannot be settled from the copybook or the data",
        evidence:{value:v, names:owner[v].join(", ")}, records:st?st.examined:0});
    });
    if(st){
      /* A coded field left as low-values in SOME records - "not set" - but not
         all of them. This rule existed in findings.py from the start and was
         never ported here, and the cross-check could not see the gap because no
         reference case had a partly-unset coded field. 17 of 18 rules matched;
         this was the 18th. */
      if(st.unset && st.examined && st.unset < st.examined){
        out.push({code:"MOSTLY_UNSET", severity:"info", field:f.name,
          claim:"this coded field carries no value in most records; low-values are how a "+
                "mainframe says 'not set'",
          evidence:{unset:st.unset.toLocaleString(), of:st.examined.toLocaleString(),
                    share:pct(st.unset, st.examined)},
          records:st.examined});
      }
      var extra = {};
      Object.keys(st.distinct).forEach(function(v){
        var t = v.trim();
        // low-values are "not set", not an undeclared code, and a value inside a
        // declared THRU range is covered even though it is not listed
        if(isUnset(v) || !t || (t in claimed) || inAnyRange(t, f)) return;
        extra[t] = st.distinct[v]; });
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

/* One field, decoded the way the tool would decode it, rendered for a table. */
function readValue(f, bytes, off){
  var len = fieldLen(f), at = off + f.offset;
  if(f.usage === "COMP-1" || f.usage === "COMP-2")
    return String(Math.round(decodeHexFloat(bytes, at, len) * 1e6) / 1e6);
  if(!f.pic) return text(bytes, at, len);
  if(f.usage === "COMP-3"){
    var packed = decodePacked(bytes, at, len, f.pic.scale || 0);
    return packed === null ? "" : String(packed);   // empty, never a plausible number
  }
  if(f.usage === "COMP"){
    var v = 0;
    for(var k = 0; k < len; k++) v = v * 256 + bytes[at + k];
    if(f.pic.signed && (bytes[at] & 0x80)) v -= Math.pow(2, 8 * len);
    return String(f.pic.scale ? v / Math.pow(10, f.pic.scale) : v);
  }
  var t = text(bytes, at, len);
  if(isUnset(t)) return "";                    // low-values: no value, not zero
  if(!f.pic.numeric) return t.replace(/[\u0000\s]+$/, "");
  var zoned = zoneSign(bytes[at + len - 1]);
  if(!zoned && isAsciiPage()) zoned = asciiZoneSign(bytes[at + len - 1]);
  var sp = zoned ? {d: t.slice(0, -1) + zoned.d, s: zoned.s} : splitSign(t);
  var digits = digitsOf(sp.d);
  // An unsigned whole number with no implied decimal is an identifier far more
  // often than a quantity - an account number, an SSN, a postcode. Stripping its
  // leading zeros changes what it IS, so those are kept as stored.
  if(!f.pic.signed && !f.pic.scale) return digits;
  var div = Math.pow(10, f.pic.scale || 0);
  var v2 = (f.pic.signed ? sp.s : 1) * parseInt(digits, 10) / div;
  return f.pic.scale ? v2.toFixed(f.pic.scale) : String(v2);
}

return {setTable:setTable, scan:scan, findings:findings, splitSign:splitSign,
        text:text, recordOffsets:recordOffsets, readValue:readValue};
})();
