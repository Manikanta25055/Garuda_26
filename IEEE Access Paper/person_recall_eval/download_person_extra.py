"""
Download 2000 more Person images from Open Images V7 validation split.
(validation/data/ already has 2500 images cached from prior run — no re-download needed)

Also attempts OI test split for additional diversity.

Target: Push Person P/R from 0.701/0.609 → 0.875+
Root cause: test set has avg 3.2 persons/image (crowded) — need more crowded training scenes.
"""

import os
os.environ["FIFTYONE_DEFAULT_DATASET_DIR"] = "/workspace/fiftyone"
os.environ["FIFTYONE_DATABASE_DIR"]        = "/workspace/fiftyone/db"

import shutil
from pathlib import Path

import fiftyone as fo
import fiftyone.zoo as foz
fo.config.default_dataset_dir = "/workspace/fiftyone"

OUT_DIR    = Path("/workspace/datasets/openimages_person_extra")
IMAGES_DIR = OUT_DIR / "images"
LABELS_DIR = OUT_DIR / "labels"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
LABELS_DIR.mkdir(parents=True, exist_ok=True)

OI_CLASS    = "Person"
YOLO_CLS_ID = 2
MIN_BOX_AREA = 0.003
TARGET = 2000

converted = 0
skipped   = 0

for split, max_s in [("validation", 2000), ("test", 500)]:
    if converted >= TARGET:
        break

    ds_name = f"oi_person_v6_{split}"
    if fo.dataset_exists(ds_name):
        fo.delete_dataset(ds_name)

    print(f"\nLoading {split} split (cached metadata, minimal download)...")
    try:
        ds = foz.load_zoo_dataset(
            "open-images-v7",
            split=split,
            label_types=["detections"],
            classes=[OI_CLASS],
            max_samples=max_s,
            seed=123,       # different seed from v5 (seed=42) → different images
            shuffle=True,
            dataset_name=ds_name,
        )
    except Exception as e:
        print(f"  Failed: {e}")
        continue

    print(f"  Loaded {len(ds)} samples. Converting...")

    for sample in ds.iter_samples(progress=True):
        if converted >= TARGET:
            break
        try:
            img_path = Path(sample.filepath)
            if not img_path.exists():
                skipped += 1
                continue

            detections = sample.ground_truth.detections if sample.ground_truth else []
            target_dets = [d for d in detections if d.label == OI_CLASS]
            if not target_dets:
                skipped += 1
                continue

            # Skip if already in existing person dataset (avoid duplicates)
            existing = Path("/workspace/datasets/openimages_extra/person/images") / img_path.name
            if existing.exists():
                skipped += 1
                continue

            out_img = IMAGES_DIR / img_path.name
            if not out_img.exists():
                shutil.copy2(img_path, out_img)

            label_path = LABELS_DIR / (img_path.stem + ".txt")
            with open(label_path, "w") as f:
                for det in target_dets:
                    x, y, w, h = det.bounding_box
                    cx = x + w / 2
                    cy = y + h / 2
                    if w * h < MIN_BOX_AREA:
                        continue
                    cx = max(0.001, min(0.999, cx))
                    cy = max(0.001, min(0.999, cy))
                    w  = max(0.001, min(0.999, w))
                    h  = max(0.001, min(0.999, h))
                    f.write(f"{YOLO_CLS_ID} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n")

            converted += 1
        except Exception:
            skipped += 1
            continue

    fo.delete_dataset(ds_name)

n_img = len(list(IMAGES_DIR.glob("*.jpg")))
print(f"\nPerson extra: {n_img} images, {converted} newly converted, {skipped} skipped")
print(f"Output: {OUT_DIR}")
