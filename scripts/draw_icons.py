#!/usr/bin/env python3
"""Draw the icons the owner has not drawn yet, in the hand of the ones he has.

    python3 scripts/draw_icons.py                # writes basic_pipelines/garuda_web/icons/*.svg
    python3 scripts/draw_icons.py mic send       # only these

scripts/build_icons.py turns the owner's iPad sheet into icons. This script
fills the gaps so the whole site is one pen: each icon is described as a few
plain strokes on the same 24 px grid, and every stroke is then redrawn the
way a hand would. The line wanders a little, ends run slightly past where
they should stop, closed shapes overlap instead of meeting, and some shapes
get a second, lighter pass. The wobble is seeded by the icon's name, so a
rebuild gives the same drawing.

An icon the owner draws later replaces the file of the same name (build_icons
writes the same folder); nothing else has to change. Never add a name here
that build_icons.py already produces.
"""
import math
import random
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "basic_pipelines/garuda_web/icons"
STROKE = 1.4             # reads the same as the drawn set: those strokes are filled outlines, a touch heavier than their 1.15 nominal
TAU = math.pi * 2


# ── shapes: each returns a list of (x, y) points ──────────────────────────────

def line(*pts):
    """A polyline through the given points; corners stay corners."""
    out = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        n = max(2, int(math.hypot(x1 - x0, y1 - y0) / 0.9))
        out += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n)]
        out.append((x1, y1))        # doubled at a corner: the curve fit keeps it sharp
    return out


def arc(cx, cy, r, a0, a1, ry=None):
    """Degrees, clockwise on screen from 3 o'clock."""
    ry = r if ry is None else ry
    n = max(4, int(abs(a1 - a0) / 360 * TAU * max(r, ry) / 0.8))
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def ring(cx, cy, r, ry=None, start=-100):
    """A circle drawn in one go: it overshoots its own start, as a hand does."""
    return arc(cx, cy, r, start, start + 372, ry)


def box(x0, y0, x1, y1, r=1.6):
    """Rounded rectangle in one stroke, closing with a small overlap."""
    pts = []
    for cx, cy, a in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        pts += arc(cx, cy, r, a, a + 90)
    return pts + line(pts[-1], (x0 + r + 1.2, y0))


# ── the hand ──────────────────────────────────────────────────────────────────

def hand(pts, rng, wobble=0.16, overshoot=0.35):
    """Redraw a clean stroke as an ink stroke."""
    phases = [(rng.uniform(0, TAU), rng.uniform(0.25, 0.5)), (rng.uniform(0, TAU), rng.uniform(0.7, 1.1))]
    out, travelled = [], 0.0
    for i, (x, y) in enumerate(pts):
        px, py = pts[max(i - 1, 0)]
        nx, ny = pts[min(i + 1, len(pts) - 1)]
        travelled += math.hypot(x - px, y - py)
        dx, dy = nx - px, ny - py
        d = math.hypot(dx, dy) or 1.0
        off = wobble * sum(math.sin(travelled * f + p) * w for (p, f), w in zip(phases, (0.7, 0.3)))
        out.append((x - dy / d * off, y + dx / d * off))
    # Ends run on a little, in the direction the pen was already moving.
    for end, prev in ((0, 1), (-1, -2)):
        (x, y), (px, py) = out[end], out[prev]
        d = math.hypot(x - px, y - py) or 1.0
        k = overshoot * rng.uniform(0.3, 1.0)
        out[end] = (x + (x - px) / d * k, y + (y - py) / d * k)
    return out


def path(pts):
    """Catmull-Rom through the points, written as cubic Beziers."""
    f = lambda v: f"{v:.2f}".rstrip("0").rstrip(".")
    d = [f"M{f(pts[0][0])} {f(pts[0][1])}"]
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = pts[max(i - 1, 0)], pts[i], pts[i + 1], pts[min(i + 2, len(pts) - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f"C{f(c1[0])} {f(c1[1])} {f(c2[0])} {f(c2[1])} {f(p2[0])} {f(p2[1])}")
    return "".join(d)


def blade(angle, reach=9.8, width=2.7):
    """One fan blade: a leaf from the hub outwards."""
    a = math.radians(angle)
    out = []
    for x, y in arc(reach / 2 + 1.2, 0, reach / 2 - 1.2, 0, 372, width):
        out.append((12 + x * math.cos(a) - y * math.sin(a), 12 + x * math.sin(a) + y * math.cos(a)))
    return out


def dot(cx, cy, r=1.25):
    return ("dot", cx, cy, r)


def light(pts):
    """A second, quicker pass: thinner and looser, like the owner's shading."""
    return ("light", pts)


def render(name, strokes):
    rng = random.Random(name)
    body = []
    for s in strokes:
        if isinstance(s, tuple) and s and s[0] == "dot":
            _, cx, cy, r = s
            blob = hand(ring(cx, cy, r * 0.55), rng, wobble=0.05, overshoot=0.05)
            body.append(f'<path d="{path(blob)}" stroke-width="{r * 1.1:.2f}"/>')
        elif isinstance(s, tuple) and s and s[0] == "light":
            body.append(f'<path d="{path(hand(s[1], rng, wobble=0.22, overshoot=0.5))}" stroke-width="{STROKE * 0.78:.2f}"/>')
        else:
            body.append(f'<path d="{path(hand(s, rng))}"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round">' + "".join(body) + "</svg>\n")


# ── the icons ─────────────────────────────────────────────────────────────────

def shield():
    return line((12, 2.8), (19.2, 5.6)) + arc(9.4, 6.5, 9.9, 0, 78, 13.6)[1:] + \
        arc(14.6, 6.5, 9.9, 102, 180, 13.6) + line((4.7, 6.5), (4.8, 5.6), (12.6, 2.6))


SLASH = line((4.2, 4.6), (19.8, 20))

ICONS = {
    # Narada's dock
    "mic": [box(8.9, 2.8, 15.1, 13.6, r=3.05), arc(12, 11, 6.3, 8, 172), line((12, 17.4), (12, 20.8)),
            line((8.6, 20.9), (15.4, 20.8)), light(line((10.6, 5.6), (10.6, 8.4)))],
    "send": [line((12, 20.4), (12, 4.4)), line((5.6, 10.6), (12, 4), (18.4, 10.6)),
             light(line((13.6, 18.6), (13.6, 12.4)))],
    "expand": [line((4.8, 15), (12, 8), (19.2, 15)), light(line((8.4, 16.6), (12, 13.2), (15.6, 16.6)))],
    "minimise": [line((4.8, 8.2), (12, 15.2), (19.2, 8.2)), line((6.2, 19.6), (17.8, 19.5))],
    # status
    # All clear: one loose circle and a tick that sweeps out of it, signed off
    # in a single go. The counterpart of the owner's warning triangle. (A
    # shield with a tick was the first try; it read as a logo, not a state.)
    "all-clear": [ring(11.4, 12.6, 8.2), line((7, 12.4), (10.8, 16.2), (20.6, 4.2)),
                  light(arc(11.4, 12.6, 5.6, 150, 215))],
    "shield": [shield(), light(line((12, 5.6), (12, 19.2)))],
    "bell": [arc(12, 11.2, 5.6, 180, 360) + line((17.6, 11.2), (17.7, 15), (19.6, 17.4), (4.4, 17.4), (6.3, 15), (6.4, 11.2)),
             arc(12, 18.4, 2, 10, 170), line((12, 3), (12, 5.4))],
    # chrome
    "power": [arc(12, 13, 7.6, -58, 238), line((12, 2.8), (12, 11.6))],
    "more": [dot(5, 12), dot(12, 12), dot(19, 12)],
    "close": [line((5.6, 5.6), (18.4, 18.4)), line((18.4, 5.4), (5.6, 18.6))],
    "users": [ring(9, 7.8, 3.3), arc(9, 20.6, 6.4, 188, 352, 7.4), arc(16.9, 8.6, 2.5, -160, 150),
              arc(17.2, 20.4, 4.6, 262, 350, 6.4)],
    "back": [line((14.6, 4.8), (7.2, 12), (14.6, 19.2))],
    "forward": [line((9.4, 4.8), (16.8, 12), (9.4, 19.2))],
    "info": [ring(12, 12, 8.6), line((12, 11), (12, 16.6)), dot(12, 7.6, 1.1)],
    # modes
    "dnd": [ring(12, 12, 8.6), line((7.4, 12), (16.6, 12)), light(line((8.6, 13.7), (15, 13.6)))],
    "privacy": [arc(12, 15.4, 9.6, 205, 335, 9.2), arc(12, 8.6, 9.6, 25, 155, 9.2), ring(12, 12, 2.7), SLASH],
    "idle": [ring(12, 12, 8.6), line((9.6, 8.4), (9.6, 15.6)), line((14.4, 8.4), (14.4, 15.6))],
    "emergency": [arc(12, 15.6, 5.2, 180, 360) + line((17.2, 15.6), (17.2, 18)), line((6.8, 15.6), (6.8, 18)),
                  line((4.6, 18.6), (19.4, 18.6)), line((12, 3), (12, 6.2)), line((4.2, 7), (6.6, 9.2)),
                  line((19.8, 7), (17.4, 9.2)), light(arc(12, 15.6, 2.4, 190, 270))],
    "email-off": [box(3, 5.6, 21, 18.4, r=1.8), line((4.2, 7.6), (12, 13.4), (19.8, 7.6)), SLASH],
    # camera controls
    "snapshot": [box(2.8, 7, 21.2, 19.4, r=2.2), line((8, 7), (9.6, 4.4), (14.4, 4.4), (16, 7)),
                 ring(12, 13.2, 3.5), light(arc(12, 13.2, 1.7, 190, 280))],
    "record": [ring(12, 12, 8.6), dot(12, 12, 3.6)],
    "fullscreen": [line((4, 9), (4, 4), (9, 4)), line((15, 4), (20, 4), (20, 9)),
                   line((20, 15), (20, 20), (15, 20)), line((9, 20), (4, 20), (4, 15))],
    # devices
    "light": [arc(12, 9.6, 5.8, 128, 412) + line((15.6, 14.2), (15.4, 16.6), (8.6, 16.6), (8.4, 14.2)),
              line((9.4, 18.8), (14.6, 18.8)), line((10.6, 21), (13.4, 21)),
              light(line((10.4, 10.4), (12, 12.4), (13.6, 10.4)))],
    "fan": [dot(12, 12, 1.5), blade(-90), blade(30), blade(150)],
    "plug": [line((9, 2.8), (9, 7.4)), line((15, 2.8), (15, 7.4)),
             line((6.2, 7.6), (17.8, 7.6), (17.8, 11.4)) + arc(12, 11.4, 5.8, 0, 180) + line((6.2, 11.4), (6.2, 7.2)),
             line((12, 17.2), (12, 21.2))],
    "sensor": [dot(12, 12, 1.5), arc(12, 12, 4.4, -50, 50), arc(12, 12, 4.4, 130, 230),
               arc(12, 12, 8.2, -42, 42), arc(12, 12, 8.2, 138, 222)],
    "scene": [line((10, 3), (11.6, 8.4), (17, 10), (11.6, 11.6), (10, 17), (8.4, 11.6), (3, 10), (8.4, 8.4), (10.2, 2.8)),
              line((18, 14.4), (18.8, 17.2), (21.6, 18), (18.8, 18.8), (18, 21.6), (17.2, 18.8), (14.4, 18), (17.2, 17.2), (18.1, 14.3))],
    "clock": [ring(12, 12, 8.6), line((12, 7), (12, 12.2), (15.6, 14.4))],
    "lock": [box(5, 10.4, 19, 20.6, r=2), arc(12, 10.4, 4.2, 180, 360, 5.4), line((12, 14.4), (12, 16.8))],
    # actions
    "add": [line((12, 4.6), (12, 19.4)), line((4.6, 12), (19.4, 12))],
    "check": [line((4.6, 12.6), (9.6, 17.6), (19.6, 6.2)), light(line((7.4, 12.2), (9.8, 14.6)))],
    "delete": [line((4, 6.6), (20, 6.6)), line((9, 6.4), (9.6, 3.8), (14.4, 3.8), (15, 6.4)),
               line((6, 6.8), (7, 20.4), (17, 20.4), (18, 6.8)), line((10, 10), (10.3, 17)), line((14, 10), (13.7, 17))],
    "edit": [line((4, 20), (5, 15.4), (16, 4.4), (19.6, 8), (8.6, 19), (3.8, 20.1)), line((13.6, 6.8), (17.2, 10.4))],
    "refresh": [arc(12, 12, 8, 55, 325), line((19.4, 3), (18.8, 8), (13.8, 7.4))],
    "download": [line((12, 3.4), (12, 15)), line((6.8, 10.2), (12, 15.6), (17.2, 10.2)),
                 line((4, 16.6), (4, 20.2), (20, 20.2), (20, 16.6))],
    "search": [ring(10.4, 10.4, 6.4), line((15.2, 15.2), (20.6, 20.6)), light(arc(10.4, 10.4, 3.8, 190, 265))],
}


def main():
    names = sys.argv[1:] or list(ICONS)
    for name in names:
        (OUT / f"{name}.svg").write_text(render(name, ICONS[name]))
    print(f"drew {len(names)} icons into {OUT}")


if __name__ == "__main__":
    main()
