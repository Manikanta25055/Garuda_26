#!/usr/bin/env python3
"""Draw the dashboard's floor plan in the same hand as the icons.

    python3 scripts/draw_floorplan.py      # writes basic_pipelines/garuda_web/floorplan.svg

The plan is described as plain strokes (walls, door swings, furniture) on a
580 x 420 sheet and every stroke is redrawn by draw_icons.hand, so the walls
wander like the icons do. It carries no colours of its own: app.js inlines
the file and style.css inks it from the theme (.fp-* classes), so it follows
light and dark. Opened on its own it is black ink on nothing.
"""
import random
from pathlib import Path

from draw_icons import arc, box, hand, line, path, ring

OUT = Path(__file__).resolve().parent.parent / "basic_pipelines/garuda_web/floorplan.svg"
K = 8.0                      # the hand is tuned for a 24 px icon: draw small, scale up
rng = random.Random("floorplan")


def ink(pts, wobble=0.13, overshoot=0.3):
    small = [(x / K, y / K) for x, y in pts]
    return path([(x * K, y * K) for x, y in hand(small, rng, wobble, overshoot)])


def big(fn, *a, **kw):
    """Icon-space shape helpers sample too coarsely at this size: build small, scale up."""
    return [(x * K, y * K) for x, y in fn(*[v / K if isinstance(v, (int, float)) else v for v in a],
                                          **{k: v / K for k, v in kw.items()})]


def seg(*pts):
    return [(x * K, y * K) for x, y in line(*[(x / K, y / K) for x, y in pts])]


def swing(hx, hy, r, a0, a1):
    """A door standing open: the leaf, and the quarter circle it sweeps from the jamb (a0)."""
    pts = [(x * K, y * K) for x, y in arc(hx / K, hy / K, r / K, a0, a1)]
    return [pts, seg((hx, hy), pts[-1])]


def window(x0, y0, x1, y1):
    return big(box, x0, y0, x1, y1, r=1.5)


# ── walls: each run stops at a doorway ─────────────────────────────────────────
WALLS = [
    # outer shell, clockwise from the main door's right jamb
    seg((112, 412), (572, 412), (572, 8), (8, 8), (8, 412), (40, 412)),
    seg((156, 8), (156, 183)),                       # bedroom 1 | bedroom 2
    seg((304, 8), (304, 183)),                       # bedroom 2 | washroom
    seg((412, 8), (412, 183)),                       # washroom | kitchen, balcony
    seg((412, 108), (424, 108)), seg((470, 108), (572, 108)),   # kitchen / balcony
    # hall, top side: three room doors, then the balcony door
    seg((8, 183), (96, 183)), seg((150, 183), (244, 183)), seg((298, 183), (326, 183)),
    seg((374, 183), (510, 183)), seg((552, 183), (572, 183)),
    # hall, living-room side: open in the middle
    seg((8, 218), (120, 218)), seg((330, 218), (572, 218)),
]

DOORS = (swing(40, 412, 72, 0, -90) + swing(150, 183, 54, 180, 270) + swing(298, 183, 54, 180, 270)
         + swing(326, 183, 48, 0, -90) + swing(552, 183, 42, 180, 270) + swing(424, 108, 46, 0, 90))

FURNITURE = [
    # living room: sofa, low table, dining table and six chairs
    big(box, 300, 344, 500, 394, r=10), seg((320, 360), (480, 360)), seg((400, 346), (400, 360)),
    big(box, 356, 286, 444, 320, r=7),
    big(box, 24, 250, 110, 306, r=6),
    *[seg((x, y), (x + 20, y)) for x in (28, 57, 86) for y in (240, 316)],
    # bedrooms: bed, pillows, side table
    *[s for x in (18, 166) for s in (
        big(box, x, 26, x + 116, 108, r=7), seg((x + 2, 52), (x + 114, 52)),
        big(box, x + 8, 32, x + 52, 46, r=4), big(box, x + 62, 32, x + 106, 46, r=4))],
    big(box, 137, 30, 152, 46, r=3), big(box, 285, 30, 300, 46, r=3),
    # washroom: basin, toilet
    big(box, 314, 16, 348, 46, r=6), big(ring, 331, 31, 5),
    big(ring, 386, 44, 15, ry=19), seg((370, 18), (402, 18)),
    # kitchen: counter, hob, sink
    seg((418, 42), (566, 42)),
    big(ring, 440, 26, 8), big(ring, 464, 26, 8), big(box, 500, 15, 540, 35, r=4),
]

WINDOWS = [window(572, 268, 578, 342), window(56, 2, 124, 8), window(204, 2, 272, 8), window(462, 2, 536, 8)]

HATCH = [seg((x, 114), (x - 62, 176)) for x in range(432, 640, 18)]   # balcony floor, clipped below

LABELS = [
    (64, 150, "Bedroom 1"), (212, 150, "Bedroom 2"), (360, 112, "Washroom"), (492, 80, "Kitchen"),
    (490, 152, "Balcony"), (64, 205, "Hall"), (206, 334, "Living Room"), (150, 402, "Entry"),
]


def main():
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 580 420" fill="none" stroke="currentColor" '
           'stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="Floor plan">',
           '<defs><clipPath id="fp-balcony"><rect x="414" y="110" width="156" height="72"/></clipPath></defs>',
           '<g class="fp-hatch" clip-path="url(#fp-balcony)" stroke-width="1" opacity="0.2">']
    out += [f'<path d="{ink(h, 0.08, 0.1)}"/>' for h in HATCH]
    out.append('</g><g class="fp-furniture" stroke-width="1.5" opacity="0.42">')
    out += [f'<path d="{ink(f)}"/>' for f in FURNITURE]
    out.append('</g><g class="fp-doors" stroke-width="1.3" opacity="0.6">')
    out += [f'<path d="{ink(p, 0.06, 0.15)}"/>' for p in DOORS]
    out.append('</g><g class="fp-windows" stroke-width="1.3" opacity="0.6">')
    out += [f'<path d="{ink(w, 0.04, 0.1)}"/>' for w in WINDOWS]
    out.append('</g><g class="fp-walls" stroke-width="3.2">')
    out += [f'<path pathLength="1" d="{ink(w)}"/>' for w in WALLS]
    out.append('</g><g class="fp-labels" stroke="none" fill="currentColor" text-anchor="middle" '
               'font-family="\'DM Sans\', system-ui, sans-serif" font-size="10.5" font-weight="600" letter-spacing="1.6">')
    out += [f'<text x="{x}" y="{y}">{t.upper()}</text>' for x, y, t in LABELS]
    out.append('</g></svg>\n')
    OUT.write_text("".join(out))
    print(f"drew the floor plan into {OUT}")


if __name__ == "__main__":
    main()
