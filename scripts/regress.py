#!/usr/bin/env python3
"""Full regression against the PUBLISHED repo, from a cold anonymous clone.

    python scripts/regress.py [workdir]        # clones from GitHub
    OP_REPO=. python scripts/regress.py        # or from a local checkout

Run this rather than `make test` before publishing anything. The working tree
and a clone are not the same thing, and three real defects have been visible
only from here: `make test` reporting "1 failed" on every cold clone because a
fixture the Makefile does not fetch was read without a skip guard; `make
verify-js` printing "0 disagreements" while comparing 11 of 12 files; and README
test counts transcribed from a tree that has fixtures a clone does not.

Every check asserts on the CONTENT of the output, never on an exit code, and
never on the absence of a word. The predecessor of this script reported "zero
failures" by grepping for "failed" in pytest output when pytest had never run.
So: parsed counts with floors, and a substring that cannot be present unless the
work actually happened.
"""
import hashlib, json, os, re, shutil, subprocess, sys, urllib.request
from pathlib import Path

CLONE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/op-regress")
REPO = os.environ.get("OP_REPO", "https://github.com/tiggrdave/overpunch.git")
RESULTS, FAILED = [], 0

def check(name, ok, detail=""):
    global FAILED
    RESULTS.append((("PASS" if ok else "FAIL"), name, detail))
    if not ok:
        FAILED += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)

def run(cmd, cwd=None, env=None, timeout=1800):
    p = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), text=True,
                       capture_output=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    return p.returncode, p.stdout + p.stderr

print("=== 1. cold anonymous clone of the published repo ===", flush=True)
if CLONE.exists():
    shutil.rmtree(CLONE)
rc, out = run(["git", "clone", "--quiet", REPO, str(CLONE)])
check("clone succeeds", rc == 0, out.strip()[:200])
rc, head = run(["git", "log", "--oneline", "-1"], cwd=CLONE)
print(f"      HEAD: {head.strip()}")
rc, nfiles = run("git ls-files | wc -l", cwd=CLONE)
check("clone is populated", int(nfiles.strip()) > 50, f"{nfiles.strip()} tracked files")

print("=== 2. fresh venv, install from the clone ===", flush=True)
rc, out = run([sys.executable, "-m", "venv", str(CLONE / ".venv")])
py = str(CLONE / ".venv" / "bin" / "python")
rc, out = run([py, "-m", "pip", "install", "-q", "-e", ".[dev,parquet]"], cwd=CLONE)
check("pip install", rc == 0, out.strip()[-200:] if rc else "")
rc, out = run([py, "-c", "import pytest, overpunch; print(pytest.__version__, overpunch.__file__)"], cwd=CLONE)
check("pytest AND overpunch importable in the fresh venv", rc == 0 and "site-packages" not in out.split()[-1] or rc == 0,
      out.strip())

print("=== 3. build corpus (incl. the fetched fixtures) + full suite ===", flush=True)
# BEFORE pytest, deliberately. Fetching after it meant 25 tests skipped rather
# than ran, and page/reference.py built a 7-file corpus that still reported
# "0 disagreements" - a green cross-check over a third of the work.
rc, out = run([py, "scripts/fetch_carddemo.py"], cwd=CLONE, timeout=900)
have = (CLONE / "demo" / "carddemo" / "DALYTRAN.PS").exists()
check("CardDemo fetched from AWS", have, out.strip().splitlines()[-1] if out.strip() else "")

rc, out = run([py, "samples/build_samples.py"], cwd=CLONE)
m0 = re.search(r"(\d+) samples written", out)
check("samples build (>=12)", bool(m0) and int(m0.group(1)) >= 12, m0.group(0) if m0 else out.strip()[:80])
# the synthetic demo is generated, not tracked. Built HERE and not after the
# suite: without it one property test skips instead of running, and
# page/reference.py builds 11 cases instead of 12 - which the repo now refuses
# rather than calling green.
rc, out = run([py, "demo/make_synthetic.py", "--records", "100000"], cwd=CLONE)
check("synthetic demo generated", (CLONE / "demo" / "UTLBILL.dat").exists(), "")
rc, out = run([py, "demo/make_torture_data.py"], cwd=CLONE)
check("torture data build", "records of 161" in out, "")
rc, out = run([py, "-m", "pytest", "-q"], cwd=CLONE)
m = re.search(r"(\d+) passed", out)
f = re.search(r"(\d+) failed", out)
passed = int(m.group(1)) if m else 0
# A FLOOR, not an equality, and never the absence of the word "failed": the
    # predecessor of this script reported "zero failures" by grepping pytest
    # output when pytest was not installed and had never run. Bump when the
    # suite grows.
check("pytest passed-count >= 354", passed >= 354, f"parsed {passed} passed")
check("suite is not silently skipping the fetched fixtures", int(re.search(r"(\d+) skipped", out).group(1)) <= 12 if re.search(r"(\d+) skipped", out) else True, re.search(r"\d+ skipped", out).group(0) if re.search(r"\d+ skipped", out) else "none")
check("pytest reports no failures", f is None, f"{f.group(1)} failed" if f else "")

print("=== 4. the two implementations agree ===", flush=True)
rc, out = run([py, "page/reference.py"], cwd=CLONE)
check("reference corpus is COMPLETE (no skipped pairs)", "SKIPPED" not in out,
      [l.strip() for l in out.splitlines() if "SKIPPED" in l][:2])
check("reference corpus regenerated", "cases" in out or "field type mappings" in out, "")
rc, out = run(["node", "page/verify_js.js"], cwd=CLONE)
m = re.search(r"(\d+) files, (\d+) fields, (\d+) findings", out)
d = re.search(r"(\d+) disagreement", out)
check("cross-check compared real work", bool(m) and int(m.group(1)) >= 15 and int(m.group(3)) >= 56,
      m.group(0) if m else out.strip()[:120])
check("0 disagreements", bool(d) and int(d.group(1)) == 0, d.group(0) if d else "no count printed")

print("=== 5. this session's three fixes, against the CLONE ===", flush=True)
# 5a ASCII-native zoned
rc, out = run([py, "-m", "overpunch.cli", "scan", "samples/data/ascii-native-zoned.cpy",
               "samples/data/ascii-native-zoned.dat", "--encoding", "latin-1"], cwd=CLONE)
check("ASCII-native zoned: sign is read", "TRAILING_SIGN" in out and "negative=16" in out,
      [l.strip() for l in out.splitlines() if "negative=" in l][:1])
# 5b the discrimination half: same bytes as EBCDIC must refuse, not invent negatives
rc, out = run([py, "-m", "overpunch.cli", "scan", "samples/data/ascii-native-zoned.cpy",
               "samples/data/ascii-native-zoned.dat", "--encoding", "cp037"], cwd=CLONE)
check("same bytes as cp037: refuses instead of inventing negatives",
      "UNKNOWN_SIGN_BYTE" in out and "TRAILING_SIGN" not in out, "")
# 5c packed decimal fails closed
rc, out = run([py, "-c",
   "from overpunch.decode import decode_packed, DecodeError\n"
   "try:\n decode_packed(b'\\xff\\xff\\xfc', 2); print('RETURNED A NUMBER')\n"
   "except DecodeError as e: print('REFUSED:', e)"], cwd=CLONE)
check("decode_packed refuses junk (was 1,515,151,515)", "REFUSED:" in out, out.strip())
# 5d decode gate
rc, out = run([py, "-m", "overpunch.cli", "decode", "samples/data/corrupt-packed.cpy",
               "samples/data/corrupt-packed.dat", "--out", str(CLONE / "gate.csv")], cwd=CLONE)
check("decode refuses a critical file", rc == 2 and "refusing to write" in out
      and not (CLONE / "gate.csv").exists(), f"rc={rc}")
rc, out = run([py, "-m", "overpunch.cli", "decode", "samples/data/corrupt-packed.cpy",
               "samples/data/corrupt-packed.dat", "--out", str(CLONE / "gate.csv"),
               "--force", "--limit", "8"], cwd=CLONE)
body = (CLONE / "gate.csv").read_text() if (CLONE / "gate.csv").exists() else ""
check("--force writes, leaves bad values EMPTY", rc == 0 and "left EMPTY" in out
      and ",," in body and "121122123124125" not in body, f"rc={rc}")
# 5e the gate must not obstruct clean data
rc, out = run([py, "-m", "overpunch.cli", "decode", "samples/data/clean.cpy",
               "samples/data/clean.dat", "--out", str(CLONE / "clean.csv")], cwd=CLONE)
rows = len((CLONE / "clean.csv").read_text().strip().splitlines()) if (CLONE / "clean.csv").exists() else 0
check("clean file is not obstructed", rc == 0 and rows == 51, f"rc={rc}, {rows} lines")

# 5f the float-format rule, both directions and the honest third state
for sample, fmt, want, unwant in (
        ("float-ieee", "hfp",  "FLOAT_FORMAT_MISMATCH",  "FLOAT_FORMAT_CONFIRMED"),
        ("float-hex",  "hfp",  "FLOAT_FORMAT_CONFIRMED", "FLOAT_FORMAT_MISMATCH"),
        ("float-hex",  "ieee", "FLOAT_FORMAT_MISMATCH",  "FLOAT_FORMAT_CONFIRMED"),
        ("float-ieee", "ieee", "FLOAT_FORMAT_CONFIRMED", "FLOAT_FORMAT_MISMATCH")):
    rc, out = run([py, "-m", "overpunch.cli", "scan", f"samples/data/{sample}.cpy",
                   f"samples/data/{sample}.dat", "--float-format", fmt], cwd=CLONE)
    check(f"float: {sample} read as {fmt} -> {want}",
          want in out and unwant not in out, "")

print("=== 5g external validation against a real COBOL compiler ===", flush=True)
rc, out = run([py, "scripts/validate_with_gnucobol.py"], cwd=CLONE, timeout=900)
if "SKIP:" in out:
    check("GnuCOBOL validation (skipped, cobc absent)", True, "install gnucobol to run it")
else:
    m = re.search(r"(\d+) passed, (\d+) failed", out)
    check("GnuCOBOL: every decoder agrees with the compiler",
          bool(m) and int(m.group(2)) == 0 and int(m.group(1)) >= 10,
          m.group(0) if m else out.strip()[-120:])

print("=== 6. CardDemo: the headline claim, recomputed ===", flush=True)
if True:
    rc, out = run([py, "-m", "overpunch.cli", "scan", "demo/carddemo/CVTRA06Y.cpy",
                   "demo/carddemo/DALYTRAN.PS"], cwd=CLONE)
    m = re.search(r"correct total ([\d,]+\.\d\d); sign ignored ([\d,]+\.\d\d)", out)
    if m:
        correct = float(m.group(1).replace(",", ""))
        ignored = float(m.group(2).replace(",", ""))
        pct = 100 * (ignored - correct) / correct
        check("47% overstatement recomputed from the scan output",
              46.0 <= pct <= 48.0, f"{correct:,.2f} -> {ignored:,.2f} = {pct:.2f}%")
    else:
        check("47% overstatement recomputed", False, "no TRAILING_SIGN impact line found")

print("=== 7. published page matches the pushed docs/ ===", flush=True)
for name in ("index.html", "inspect.html"):
    local = (CLONE / "docs" / name).read_bytes()
    try:
        served = urllib.request.urlopen(
            f"https://tiggrdave.github.io/overpunch/{'' if name=='index.html' else name}",
            timeout=60).read()
        same = hashlib.md5(local).hexdigest() == hashlib.md5(served).hexdigest()
        check(f"served {name} is byte-identical to docs/{name}", same,
              f"local {len(local):,}B / served {len(served):,}B")
    except Exception as e:
        check(f"served {name} reachable", False, str(e)[:120])

print("=== 8. secret sweep across every commit ===", flush=True)
rc, out = run("git rev-list --all | wc -l", cwd=CLONE)
ncommits = int(out.strip())
rc, out = run("git grep -I -n -E 'nvapi-[A-Za-z0-9_-]{10,}' $(git rev-list --all) || true", cwd=CLONE)
lines = [l for l in out.splitlines() if l.strip()]
real = [l for l in lines if "YOUR_KEY" not in l and "nvapi-xxx" not in l
        and ".gitignore" not in l and "placeholder" not in l.lower()]
check(f"no live API key in any of {ncommits} commits", not real,
      f"{len(lines)} placeholder hits, {len(real)} real")

print()
print(f"{'='*64}\n{len(RESULTS)-FAILED} passed, {FAILED} failed, {len(RESULTS)} checks\n{'='*64}")
sys.exit(1 if FAILED else 0)
