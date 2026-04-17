"""
Build dataset v6 — adds 2000 more diverse Person images on top of v5 dataset.

Sources:
  1. /workspace/datasets/dataset_v5/         (existing 6,226-image v5 dataset)
  2. /workspace/datasets/openimages_person_extra/  (2,000 new Person images)

Target: Push Person P/R from 0.701/0.609 → 0.875+ overall P/R → 87.5-95%
"""

import random
import shutil
from pathlib import Path
from collections import defaultdict

random.seed(99)

V5_DIR      = Path("/workspace/datasets/dataset_v5")
EXTRA_DIR   = Path("/workspace/datasets/openimages_person_extra")
OUT_DIR     = Path("/workspace/datasets/dataset_v6")
IMG_EXTS    = {".jpg", ".jpeg", ".png", ".bmp"}
SPLITS      = {"train": 0.80, "val": 0.10, "test": 0.10}

for split in SPLITS:
    (OUT_DIR / split / "images").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / split / "labels").mkdir(parents=True, exist_ok=True)

def collect(img_dir, lbl_dir, prefix):
    samples = []
    for img in sorted(img_dir.iterdir()):
        if img.suffix.lower() not in IMG_EXTS:
            continue
        lbl = lbl_dir / (img.stem + ".txt")
        if not lbl.exists():
            continue
        lines = lbl.read_text().strip().splitlines()
        if not lines:
            continue
        cc = defaultdict(int)
        for l in lines:
            p = l.split()
            if len(p) >= 5:
                try: cc[int(p[0])] += 1
                except: pass
        if not cc:
            continue
        samples.append((img, lbl, max(cc, key=cc.get), prefix))
    return samples

def copy_sample(img, lbl, prefix, di, dl):
    stem = f"{prefix}_{img.stem}"
    di_path = di / f"{stem}{img.suffix}"
    dl_path = dl / f"{stem}.txt"
    c = 0
    while di_path.exists():
        c += 1
        di_path = di / f"{stem}_{c}{img.suffix}"
        dl_path = dl / f"{stem}_{c}.txt"
    shutil.copy2(img, di_path)
    shutil.copy2(lbl, dl_path)

# ── Collect v5 data ───────────────────────────────────────────────────────────
print("Collecting v5 dataset...")
all_samples = []
for split in ["train", "val", "test"]:
    idir = V5_DIR / split / "images"
    ldir = V5_DIR / split / "labels"
    if idir.exists():
        s = collect(idir, ldir, f"v5_{split}")
        all_samples.extend(s)
        print(f"  {split}: {len(s)}")

# ── Collect extra Person images ───────────────────────────────────────────────
print("\nCollecting extra Person images...")
extra = collect(EXTRA_DIR / "images", EXTRA_DIR / "labels", "oi_person_extra")
print(f"  Person extra: {len(extra)}")

# ── Merge and re-split ────────────────────────────────────────────────────────
all_data = all_samples + extra
random.shuffle(all_data)
n = len(all_data)
n_val   = int(n * 0.10)
n_test  = int(n * 0.10)
n_train = n - n_val - n_test

assignment = ["train"]*n_train + ["val"]*n_val + ["test"]*n_test
random.shuffle(assignment)

print(f"\nTotal: {n}  →  train={n_train}, val={n_val}, test={n_test}")

sc = defaultdict(int)
cc = defaultdict(lambda: defaultdict(int))
for (img, lbl, primary, prefix), split in zip(all_data, assignment):
    copy_sample(img, lbl, prefix, OUT_DIR/split/"images", OUT_DIR/split/"labels")
    sc[split] += 1
    cc[split][primary] += 1

# ── data.yaml ─────────────────────────────────────────────────────────────────
(OUT_DIR / "data.yaml").write_text(
    f"train: {OUT_DIR}/train/images\n"
    f"val:   {OUT_DIR}/val/images\n"
    f"test:  {OUT_DIR}/test/images\n\n"
    f"nc: 4\n"
    f"names: ['Hammer', 'Knife', 'Person', 'scissors']\n"
)

CN = {0:"Hammer", 1:"Knife", 2:"Person", 3:"scissors"}
print("\n=== Dataset v6 Summary ===")
for sp in ["train","val","test"]:
    print(f"\n  {sp}: {sc[sp]} images")
    for cid, cname in CN.items():
        print(f"    {cname:8s}: {cc[sp].get(cid,0)} primary-class images")

print("\nInstance counts (train):")
for cid in range(4):
    cnt = sum(1 for lf in (OUT_DIR/"train"/"labels").glob("*.txt")
              for ln in lf.read_text().strip().splitlines()
              if ln.split() and int(ln.split()[0]) == cid)
    print(f"  {CN[cid]:8s}: {cnt:,}")

print(f"\ndata.yaml → {OUT_DIR}/data.yaml")
