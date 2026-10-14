#!/usr/bin/env python3
"""Draw Drishti's app icons: a hand-written "D" on an ink tile.

    python3 scripts/draw_app_icons.py      # writes basic_pipelines/garuda_web/drishti/*

The site's icons are the owner's pen: one even line, round ends, a small
wobble, ends that run past where they stop. scripts/draw_icons.py draws in
that hand, and this uses the same hand (its line, arc and hand functions,
seeded by the name) to write a D in one stroke: up the stem, over the top,
round the bowl, and back past the stem the way a hand closes a letter.

The composition pairs with Garuda's "G" tile: a rounded tile, a top-to-
bottom gradient, one white letter in the middle. Drishti is black and white,
so the tile runs from graphite to ink. Corners are a superellipse
(continuous, as Apple draws them). The line is heavier at 16 and 32 px, or
it thins to nothing in a tab.

Icons that a platform masks itself (apple-touch-icon, maskable) are full
bleed: iOS fills transparent corners with black, and Android crops the
maskable one to its own shape. The others carry their own corners.
"""
import io
import sys
from pathlib import Path

import cairosvg
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import draw_icons as pen  # noqa: E402  (the site's hand)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "basic_pipelines/garuda_web/drishti"

TOP, BOTTOM = (58, 58, 60), (8, 8, 9)        # graphite to ink
INK = "#FFFFFF"
SCALE = 0.66                                  # the 24 px drawing's share of the tile
STROKE = {16: 2.7, 32: 2.2}                   # in 24 px units; 1.8 above 32 px (the site's is 1.4)
SS = 4                                        # supersampling

# One stroke, as a hand writes a D.
D = [pen.line((6.6, 19.5), (6.6, 4.5), (10.6, 4.5)) + pen.arc(10.6, 12, 7.5, -90, 90)[1:]
     + pen.line((10.6, 19.5), (6.0, 19.45))]


def mark(px, stroke):
    pen.STROKE = stroke
    svg = pen.render("drishti", D).replace("currentColor", INK)
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=px, output_height=px)
    return Image.open(io.BytesIO(png)).convert("RGBA")


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
    m = mark(round(s * SCALE), STROKE.get(size, 1.8))
    img.alpha_composite(m, ((s - m.width) // 2, (s - m.height) // 2))
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
    tile(48, False).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)],
                         append_images=[tile(16, False), tile(32, False)])
    print(f"drew Drishti icons into {OUT}")


if __name__ == "__main__":
    main()
