#!/usr/bin/env python3
"""Turn the owner's iPad icon sheet into web icons.

    python3 scripts/build_icons.py                       # custom_icons_interface.pdf, page 2
    python3 scripts/build_icons.py sheet.pdf --page 2 --out basic_pipelines/garuda_web/icons

The iPad exports strokes as vectors, so nothing is traced: each icon's paths
are cut out of the page by position, recoloured to currentColor (the page's
text colour), fitted to a 24 px grid at equal optical size, and given the
same outline weight. White paint is dropped (it was background). Needs
pdftocairo (poppler-utils).

The sheet layout is two columns of labelled rows; `names` lists them top to
bottom. Add a name to `names` below when a new icon is drawn in the same place.
"""
import argparse, re, statistics, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
ap.add_argument("pdf", nargs="?", default=str(ROOT / "custom_icons_interface.pdf"))
ap.add_argument("--page", type=int, default=2)
ap.add_argument("--out", default=str(ROOT / "basic_pipelines/garuda_web/icons"))
args = ap.parse_args()
OUT = args.out
with tempfile.TemporaryDirectory() as tmp:
    svg_path = Path(tmp) / "page.svg"
    subprocess.run(["pdftocairo", "-svg", "-f", str(args.page), "-l", str(args.page),
                    args.pdf, str(svg_path)], check=True)
    src = svg_path.read_text()
paths = re.findall(r'<path ([^>]*?)/>', src)
items = []
for attrs in paths:
    d = re.search(r'\bd="([^"]*)"', attrs).group(1)
    tm = re.search(r'transform="matrix\(([^)]*)\)"', attrs)
    a, b, c, dd, e, f = map(float, tm.group(1).split(",")) if tm else (1, 0, 0, 1, 0, 0)
    nums = list(map(float, re.findall(r'-?\d+\.?\d*(?:e-?\d+)?', d)))
    xs = [a*x + c*y + e for x, y in zip(nums[0::2], nums[1::2])]
    ys = [b*x + dd*y + f for x, y in zip(nums[0::2], nums[1::2])]
    if not xs: continue
    bb = (min(xs), min(ys), max(xs), max(ys))
    if bb[2] - bb[0] > 200: continue
    style = re.search(r'style="([^"]*)"', attrs).group(1)
    # White paint was background (e.g. the lens face): leave it see-through.
    if re.search(r'fill:rgb\(9\d(\.\d+)?%,100%,100%\)', style): continue
    sw = float((re.search(r'stroke-width:([\d.]+)', style) or [0, 0])[1] or 0)
    items.append(dict(d=d, m=(a, b, c, dd, e, f), style=style, sw=sw, bb=bb,
                      cx=(bb[0]+bb[2])/2, cy=(bb[1]+bb[3])/2))
cols = {"L": (178, 240), "R": (380, 440)}
names = {"L": ["documentation", "feedback", "sign-in", "home", "devices", "automate",
               "insights", "narada", "mail", "logs"],
         "R": ["commands", "settings", "stop", "light-mode", "night-mode", "camera"]}
BOX, AREA, MAXF, STROKE = 24.0, 0.82, 0.96, 1.15   # optical size, max extent, stroke in px@24
def rnd(m): return f"{float(m.group(0)):.2f}".rstrip("0").rstrip(".")
for col, (x0, x1) in cols.items():
    sel = sorted([i for i in items if x0 <= i["cx"] <= x1 and i["cy"] < 600], key=lambda i: i["cy"])
    groups = []
    for it in sel:
        if groups and it["bb"][1] - max(g["bb"][3] for g in groups[-1]) < 8:
            groups[-1].append(it)
        else:
            groups.append([it])
    for name, g in zip(names[col], groups):
        mx = min(i["bb"][0] for i in g); my = min(i["bb"][1] for i in g)
        Mx = max(i["bb"][2] for i in g); My = max(i["bb"][3] for i in g)
        w, h = Mx - mx, My - my
        # Equal optical area, but never past the box edge.
        s = min(AREA * BOX / (w * h) ** 0.5, MAXF * BOX / max(w, h))
        tx = (BOX - w * s) / 2 - mx * s; ty = (BOX - h * s) / 2 - my * s
        main_sw = max([i["sw"] * i["m"][0] for i in g if i["sw"]] or [1])  # the outline
        k = STROKE / (main_sw * s)          # page-units -> final px
        body = []
        for i in g:
            a, b, c, dd, e, f = i["m"]
            # Fold page transform and normalisation into one matrix.
            A, B, C, D = a*s, b*s, c*s, dd*s
            E, F = e*s + tx, f*s + ty
            st = re.sub(r'rgb\([^)]*\)', 'currentColor', i["style"])
            st = re.sub(r'stroke-width:[\d.]+', lambda m: f"stroke-width:{i['sw']*k:.3f}", st)
            st = re.sub(r'stroke-miterlimit:[\d.]+;?', '', st).replace(" ", "")
            d = re.sub(r'-?\d+\.\d+', rnd, i["d"])
            body.append(f'<path style="{st}" transform="matrix({A:.5f},{B:.5f},{C:.5f},{D:.5f},{E:.3f},{F:.3f})" d="{d}"/>')
        open(f"{OUT}/{name}.svg", "w").write(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">'
            + "".join(body) + '</svg>\n')
        print(f"{name:14s} scale={s:.3f} extent={w*s:.1f}x{h*s:.1f}")
