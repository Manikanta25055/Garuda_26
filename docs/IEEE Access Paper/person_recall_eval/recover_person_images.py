"""
Recover Person images from the fiftyone cache that were downloaded but not
converted due to the dataset_dir bug.

Loads the fiftyone 'open-images-v7' train dataset (already cached) and
iterates all Person-annotated samples, copying them to our YOLO output dir.
"""

import os
os.environ["FIFTYONE_DEFAULT_DATASET_DIR"] = "/workspace/fiftyone"
os.environ["FIFTYONE_DATABASE_DIR"]        = "/workspace/fiftyone/db"

import shutil
from pathlib import Path

OUT_DIR    = Path("/workspace/datasets/openimages_extra/person")
IMAGES_DIR = OUT_DIR / "images"
LABELS_DIR = OUT_DIR / "labels"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
LABELS_DIR.mkdir(parents=True, exist_ok=True)

OI_CLASS    = "Person"
YOLO_CLS_ID = 2
MIN_BOX_AREA = 0.003

import fiftyone as fo
import fiftyone.zoo as foz

fo.config.default_dataset_dir = "/workspace/fiftyone"

# Check what's already been downloaded
train_data_dir = Path("/workspace/fiftyone/open-images-v7/train/data")
val_data_dir   = Path("/workspace/fiftyone/open-images-v7/validation/data")

print(f"Images in fiftyone train cache: {len(list(train_data_dir.glob('*.jpg'))) if train_data_dir.exists() else 0}")
print(f"Images in fiftyone val cache:   {len(list(val_data_dir.glob('*.jpg'))) if val_data_dir.exists() else 0}")

converted = 0
skipped   = 0

for split in ["train", "validation"]:
    split_data_dir = Path(f"/workspace/fiftyone/open-images-v7/{split}/data")
    if not split_data_dir.exists():
        continue

    ds_name = f"oi_person_recover_{split}"
    if fo.dataset_exists(ds_name):
        fo.delete_dataset(ds_name)

    print(f"\nLoading {split} split from fiftyone cache (no network download)...")
    try:
        ds = foz.load_zoo_dataset(
            "open-images-v7",
            split=split,
            label_types=["detections"],
            classes=[OI_CLASS],
            max_samples=2500,
            seed=42,
            shuffle=True,
            dataset_name=ds_name,
        )
    except Exception as e:
        print(f"  Failed to load {split}: {e}")
        continue

    print(f"  Loaded {len(ds)} samples. Converting...")

    for sample in ds.iter_samples(progress=True):
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

            out_img = IMAGES_DIR / img_path.name
            if out_img.exists():
                converted += 1
                continue  # already converted

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

    if converted >= 2000:
        print(f"  Got enough Person images ({converted}), stopping.")
        break

print(f"\nPerson images converted: {converted}  Skipped: {skipped}")
print(f"Output: {OUT_DIR}")
