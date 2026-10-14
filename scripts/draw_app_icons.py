#!/usr/bin/env python3
"""Draw Drishti's app icons: the Garuda "G" tile, in Drishti's ink.

    python3 scripts/draw_app_icons.py      # writes basic_pipelines/garuda_web/drishti/*

Same composition as Garuda's icon (a rounded tile, a top-to-bottom gradient,
one bold white letter in the middle), so the two products read as a pair.
Drishti is black and white, so the tile is graphite to ink. The letter is
the site's own face, DM Sans Black (1000): its stem is 0.24 of the cap height, the G's 0.26;
700 and 800 read lighter than the G.
Corners are a superellipse (continuous, as Apple draws them), not a
circular arc. At 16 and 32 px the letter is drawn larger, as type is at
small sizes, or it thins to a smudge in a tab.

Icons that a platform masks itself (apple-touch-icon, maskable) are full
bleed: iOS fills transparent corners with black, and Android crops the
maskable one to its own shape. The others carry their own corners.
"""
import io
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "basic_pipelines/garuda_web"
OUT = WEB / "drishti"
FONT = WEB / "fonts/dm-sans-var.woff2"

TOP, BOTTOM = (58, 58, 60), (8, 8, 9)        # graphite to ink
INK = (255, 255, 255)
LETTER = "D"
CAP = {16: 0.56, 32: 0.46}                    # cap height / tile; 0.36 (Garuda's G) above 32 px
WEIGHT = 1000
SS = 4                                        # supersampling


_FONT = {}


def font(px):
    if px in _FONT:
        return _FONT[px]
    var = TTFont(FONT)
    bold = instancer.instantiateVariableFont(var, {"wght": WEIGHT})
    buf = io.BytesIO()
    bold.flavor = None
    bold.save(buf)
    buf.seek(0)
    _FONT[px] = ImageFont.truetype(buf, px)
    return _FONT[px]


def squircle(size, n=4.2):
    """Mask of |x|^n + |y|^n <= 1: the continuous corner."""
    m = Image.new("L", (size, size), 0)
    px = m.load()
    h = (size - 1) / 2
    for y in range(size):
        ny = abs((y - h) / h) ** n
        for x in range(size):
            if abs((x - h) / h) ** n + ny <= 1:
                px[x, y] = 255
    return m


def tile(size, full_bleed):
    s = size * SS
    grad = Image.new("RGB", (1, s))
    for y in range(s):
        t = y / (s - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    img = grad.resize((s, s)).convert("RGBA")

    # The letter sized by its cap height and centred on its own ink box, so
    # it sits in the optical middle rather than where the font's metrics say.
    f = font(100)
    box = ImageDraw.Draw(img).textbbox((0, 0), LETTER, font=f)
    f = font(round(100 * CAP.get(size, 0.36) * s / (box[3] - box[1])))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = d.textbbox((0, 0), LETTER, font=f)
    d.text(((s - (x1 - x0)) / 2 - x0, (s - (y1 - y0)) / 2 - y0), LETTER, font=f, fill=INK)

    if not full_bleed:
        img.putalpha(squircle(s))
    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(exist_ok=True)
    tile(64, False).save(OUT / "favicon.png")
    tile(180, True).convert("RGB").save(OUT / "apple-touch-icon.png")
    tile(192, False).save(OUT / "icon-192.png")
    tile(512, False).save(OUT / "icon-512.png")
    tile(512, True).convert("RGB").save(OUT / "icon-maskable-512.png")
    # .ico: each size drawn on its own, not scaled from one, so 16 px stays crisp
    big = tile(48, False)
    big.save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)],
             append_images=[tile(16, False), tile(32, False)])
    print(f"drew Drishti icons into {OUT}")


if __name__ == "__main__":
    main()
