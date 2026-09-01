"""Render a copybook the way it usually arrives: as a scan of a printout.

A great many copybooks exist only on paper, or as an image inside a PDF nobody
can select text out of. This produces that artefact honestly - greenbar-ish
paper, a monospace listing, and the wear a real scan carries: a slight skew,
speckle, uneven exposure and a fold shadow.

Build-time only. Pillow is not needed to USE overpunch, only to manufacture this
demonstration input.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def render(text: str, out: Path, size: int = 19, skew: float = 0.45,
           seed: int = 7, clean: bool = False) -> Path:
    rng = random.Random(seed)
    lines = text.rstrip("\n").split("\n")
    path = FONT if Path(FONT).exists() else FALLBACK
    font = ImageFont.truetype(path, size)

    pad, lh = 46, int(size * 1.52)
    width = max(int(font.getlength(l)) for l in lines) + pad * 2
    height = lh * len(lines) + pad * 2
    img = Image.new("L", (width, height), 252)
    d = ImageDraw.Draw(img)

    # the pale bands of fanfold listing paper, every three lines
    if not clean:
        for i in range(len(lines)):
            if (i // 3) % 2 == 0:
                d.rectangle([0, pad + i * lh - 3, width, pad + (i + 1) * lh - 3], fill=246)

    for i, line in enumerate(lines):
        d.text((pad, pad + i * lh), line, font=font, fill=28 if clean else 34)

    if clean:
        img.save(out)
        return out

    # a fold shadow down the middle of the page
    shade = Image.new("L", img.size, 0)
    ds = ImageDraw.Draw(shade)
    ds.rectangle([width // 2 - 22, 0, width // 2 + 22, height], fill=16)
    img = Image.composite(Image.new("L", img.size, 210), img,
                          shade.filter(ImageFilter.GaussianBlur(18)))

    img = img.rotate(skew, resample=Image.BICUBIC, expand=True, fillcolor=250)
    img = img.filter(ImageFilter.GaussianBlur(0.4))

    px = img.load()
    for _ in range(int(width * height * 0.0016)):          # scanner speckle
        x, y = rng.randrange(img.width), rng.randrange(img.height)
        px[x, y] = rng.choice((70, 90, 120, 200))

    img.save(out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("copybook")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--clean", action="store_true",
                    help="no paper texture, skew or speckle")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    out = render(Path(args.copybook).read_text(), Path(args.out),
                 seed=args.seed, clean=args.clean)
    kb = out.stat().st_size / 1024
    with Image.open(out) as im:
        print(f"{out}  {im.width}x{im.height}  {kb:,.0f} KB")


if __name__ == "__main__":
    main()
