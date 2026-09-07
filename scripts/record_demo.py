"""Record the demo video by driving the published page, so it cannot drift.

The video is not a screen capture someone took once and then edited. It is
produced by this script, against the real page, using the sample files in this
repository. If the tool changes its answers, re-running this changes the video
with them - the same reason page/build.py regenerates the demo page from real
tool output instead of hand-written HTML.

It is silent on purpose: captions are drawn into the frame, so it reads with the
sound off.

    python scripts/fetch_carddemo.py          # the sample files, downloaded on demand
    pip install playwright && playwright install chromium ffmpeg
    python scripts/record_demo.py             # writes docs/overpunch-demo.mp4

Pass a URL to record a different build:

    python scripts/record_demo.py http://localhost:8000/inspect.html
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARDDEMO = ROOT / "demo" / "carddemo"
OUT = ROOT / "docs" / "overpunch-demo.mp4"
URL = "https://tiggrdave.github.io/overpunch/inspect.html"

CSS = """
#capbar{position:fixed;left:0;right:0;bottom:0;z-index:2147483647;pointer-events:none;
  background:linear-gradient(transparent,rgba(8,10,14,.93) 28%);
  padding:44px 60px 40px;opacity:0;transition:opacity .45s ease;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,system-ui,sans-serif;}
#capbar.on{opacity:1}
#capbar b{display:block;color:#fff;font-size:34px;line-height:1.22;font-weight:650;
  letter-spacing:-.4px;text-shadow:0 2px 18px rgba(0,0,0,.9)}
#capbar i{display:block;margin-top:10px;color:#8ee6c0;font-size:22px;font-style:normal;
  font-weight:500;font-variant-numeric:tabular-nums;text-shadow:0 2px 14px rgba(0,0,0,.9)}
#cardv{position:fixed;inset:0;z-index:2147483646;background:#0b0d11;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  opacity:0;transition:opacity .5s ease;pointer-events:none;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,system-ui,sans-serif;}
#cardv.on{opacity:1}
#cardv h1{color:#fff;font-size:52px;font-weight:680;letter-spacing:-1.2px;margin:0;
  text-align:center;max-width:1020px;line-height:1.18}
#cardv h2{color:#8ee6c0;font-size:27px;font-weight:500;margin:26px 0 0;text-align:center;
  max-width:900px;line-height:1.4}
#hl{position:fixed;z-index:2147483645;border:3px solid #8ee6c0;border-radius:10px;
  box-shadow:0 0 0 9999px rgba(6,8,12,.60);opacity:0;transition:opacity .4s ease;
  pointer-events:none}
#hl.on{opacity:1}
"""

# The caption bar must be click-through. Without pointer-events:none it silently
# swallows clicks on the page beneath it, Playwright retries them, and the
# recording still exits 0 with a plausible file - a failure the exit code hides.
JS = """
(()=>{const d=document;
 const b=d.createElement('div');b.id='capbar';b.innerHTML='<b></b><i></i>';d.body.appendChild(b);
 const c=d.createElement('div');c.id='cardv';c.innerHTML='<h1></h1><h2></h2>';d.body.appendChild(c);
 const h=d.createElement('div');h.id='hl';d.body.appendChild(h);
 window.__cap=(t,s)=>{b.querySelector('b').textContent=t||'';
   b.querySelector('i').textContent=s||'';b.classList.add('on');};
 window.__capoff=()=>b.classList.remove('on');
 window.__card=(t,s)=>{c.querySelector('h1').textContent=t||'';
   c.querySelector('h2').textContent=s||'';c.classList.add('on');};
 window.__cardoff=()=>c.classList.remove('on');
 window.__hl=(sel)=>{const e=d.querySelector(sel);if(!e)return false;
   const r=e.getBoundingClientRect();h.style.left=(r.left-8)+'px';h.style.top=(r.top-8)+'px';
   h.style.width=(r.width+16)+'px';h.style.height=(r.height+16)+'px';h.classList.add('on');return true;};
 window.__hloff=()=>h.classList.remove('on');
})()
"""


async def _record(url: str, raw_dir: Path) -> Path:
    from playwright.async_api import async_playwright

    async def cap(pg, t, s=None, hold=0):
        await pg.evaluate("([t,s])=>window.__cap(t,s)", [t, s])
        if hold:
            await pg.wait_for_timeout(hold)

    async def card(pg, t, s=None, hold=3400):
        await pg.evaluate("([t,s])=>window.__card(t,s)", [t, s])
        await pg.wait_for_timeout(hold)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(raw_dir),
            record_video_size={"width": 1280, "height": 720})
        pg = await ctx.new_page()
        await pg.goto(url, wait_until="networkidle", timeout=60000)
        await pg.add_style_tag(content=CSS)
        await pg.evaluate(JS)
        await pg.wait_for_timeout(600)

        await card(pg, "A mainframe extract is just bytes.",
                   "A COBOL copybook is the only thing that says what they mean.", 3800)
        await card(pg, "If the copybook is wrong about the file,",
                   "most readers still hand you a neat table.", 3600)
        await pg.evaluate("window.__cardoff()")
        await pg.wait_for_timeout(700)

        # The wrong file, from the right system: a 350-byte record does not
        # divide 15,000 bytes, and the tool says so instead of guessing.
        await cap(pg, "Two files from the same mainframe system.",
                  "AWS CardDemo - a public sample dataset", 2600)
        await pg.set_input_files("#in-cpy", str(CARDDEMO / "CVTRA06Y.cpy"))
        await pg.wait_for_timeout(900)
        await cap(pg, "The transaction copybook...", "CVTRA06Y.cpy", 2200)
        await pg.set_input_files("#in-dat", str(CARDDEMO / "ACCTDATA.PS"))
        await pg.wait_for_timeout(900)
        await cap(pg, "...pointed at the account file.",
                  "ACCTDATA.PS - the wrong file, from the right system", 3000)
        await pg.evaluate("window.__capoff()")
        await pg.wait_for_timeout(400)
        await pg.click("#run-analyze")
        await pg.wait_for_timeout(2600)

        await cap(pg, "It refuses.", "LAYOUT_MISMATCH", 3000)
        await pg.evaluate("window.__hl('.find.critical')")
        await pg.wait_for_timeout(2600)
        await cap(pg, "The copybook describes a 350-byte record.",
                  "15,000 bytes / 350 leaves 300 over. It cannot be this layout.", 4200)
        await pg.evaluate("window.__hloff()")
        await cap(pg, "No table. No guess. No silent nonsense.",
                  "It states what is wrong, and shows the arithmetic.", 3800)
        await cap(pg, "Then it offers the record lengths that would fit.",
                  "The refusal is the useful part.", 3600)
        await pg.evaluate("window.__capoff()")
        await pg.wait_for_timeout(600)

        await card(pg, "Now the file this copybook does describe.", None, 3000)
        await pg.evaluate("window.__cardoff()")
        await pg.wait_for_timeout(700)
        await pg.click("#run-clear")
        await pg.wait_for_timeout(900)
        await pg.set_input_files("#in-cpy", str(CARDDEMO / "CVTRA06Y.cpy"))
        await pg.wait_for_timeout(700)
        await pg.set_input_files("#in-dat", str(CARDDEMO / "DALYTRAN.PS"))
        await pg.wait_for_timeout(700)
        await cap(pg, "Same copybook. The daily transaction file.",
                  "DALYTRAN.PS - 105,000 bytes / 350 = 300 records", 3000)
        await pg.evaluate("window.__capoff()")
        await pg.click("#run-analyze")
        await pg.wait_for_timeout(3000)

        await cap(pg, "It decodes - and finds the trap.", "", 2400)
        await pg.evaluate("""()=>{const e=[...document.querySelectorAll('.fcode')]
          .find(x=>x.textContent.includes('TRAILING_SIGN'));
          if(e) e.closest('.find').scrollIntoView({block:'center'});}""")
        await pg.wait_for_timeout(1400)
        await cap(pg, "One byte per record carries the sign.",
                  "Read the digits only and every negative flips positive.", 4000)
        await cap(pg, "Correct total: 104,801.54",
                  "Sign ignored: 153,600.12 - overstated by 48,798.58", 4400)
        await cap(pg, "A 47% overstatement.",
                  "In AWS's own published sample data. Nothing threw an error.", 4200)
        await pg.evaluate("window.__capoff()")
        await pg.wait_for_timeout(600)

        await card(pg, "Nemotron proposes. The bytes decide.",
                   "Every model proposal is adjudicated against the file. A hallucination "
                   "can only ever become a REFUTED hypothesis.", 5000)
        await card(pg, "overpunch",
                   "github.com/tiggrdave/overpunch   -   runs entirely in your browser", 4600)
        await pg.wait_for_timeout(500)
        await ctx.close()
        await browser.close()

    webm = sorted(raw_dir.glob("*.webm"))
    if not webm:
        sys.exit("no video was recorded")
    return webm[0]


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else URL
    if not (CARDDEMO / "DALYTRAN.PS").exists():
        sys.exit("run scripts/fetch_carddemo.py first - the sample files are not vendored")
    with tempfile.TemporaryDirectory() as tmp:
        raw = asyncio.run(_record(url, Path(tmp)))
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
             "-c:v", "libx264", "-preset", "slow", "-crf", "20",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(OUT)],
            check=True)
    # Assert on the artefact, not on the exit code: a truncated or empty file
    # would still leave ffmpeg returning 0 on some inputs.
    size = OUT.stat().st_size
    if size < 500_000:
        sys.exit(f"{OUT} is only {size:,} bytes - the recording did not work")
    print(f"wrote {OUT}  ({size:,} bytes)")
    if shutil.which("ffprobe"):
        subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=width,height,codec_name",
                        "-of", "default=noprint_wrappers=1", str(OUT)])


if __name__ == "__main__":
    main()
