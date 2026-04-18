"""Open-set evaluation of MobileNetV2 weapon verifier on household-clutter crops.

Setup: 200 Open Images V7 validation images downloaded for the categories
{Coffee cup, Mobile phone, Bottle, Wrench, Screwdriver} via FiftyOne. None
contain weapons. The verifier (mobilenetv2_garuda.hef) takes a 224x224 RGB
crop and outputs Safe / Weapon. Every clutter crop should land in Safe.

This complements the in-distribution closed-set evaluation (Sec. VI-M, 99.0%
on the 500-crop balanced split): it tests how the verifier behaves when the
upstream YOLO Stage 1 false-fires a weapon class on an everyday object.
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from hailo_platform import (
    HEF, VDevice, ConfigureParams, HailoStreamInterface,
    InferVStreams, InputVStreamParams, OutputVStreamParams, FormatType,
)

ROOT = Path("/home/manikanta/Projects/Garuda_26/evaluation/openset_eval")
DATA = ROOT / "fo_data/open-images-v7/validation"
LABELS_CSV = DATA / "labels/detections.csv"
CLASSES_CSV = DATA / "metadata/classes.csv"
IMG_DIR = DATA / "data"
HEF_PATH = "/home/manikanta/Projects/Garuda_26/resources/mobilenetv2_garuda.hef"
OUT_JSON = ROOT / "openset_results.json"

TARGETS = {
    "/m/02p5f1q": "Coffee cup",
    "/m/050k8":   "Mobile phone",
    "/m/04dr76w": "Bottle",
    "/m/01j5ks":  "Wrench",
    "/m/01bms0":  "Screwdriver",
}
CLASS_NAMES = ["Safe", "Weapon"]
MIN_BOX_PX = 32   # skip tiny crops (mirrors deployed cascade behaviour)


def softmax(x):
    x = x.astype(np.float32) - x.max()
    e = np.exp(x)
    return e / e.sum()


def main():
    # Limit detections.csv to (image_id, label) we care about, for images we have on disk
    have_imgs = {p.stem: p for p in IMG_DIR.glob("*.jpg")}
    print(f"Local OI images: {len(have_imgs)}")

    crops = []  # list of (img_path, mid, display_name, (x1,y1,x2,y2))
    with open(LABELS_CSV) as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            img_id = row["ImageID"]
            mid = row["LabelName"]
            if img_id not in have_imgs or mid not in TARGETS:
                continue
            img_path = have_imgs[img_id]
            crops.append((img_path, mid, TARGETS[mid],
                          (float(row["XMin"]), float(row["YMin"]),
                           float(row["XMax"]), float(row["YMax"]))))

    print(f"Candidate crops: {len(crops)}")

    hef = HEF(HEF_PATH)
    with VDevice() as vdev:
        cfg = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
        ng = vdev.configure(hef, cfg)[0]
        ivp = InputVStreamParams.make(ng, format_type=FormatType.UINT8)
        ovp = OutputVStreamParams.make(ng, format_type=FormatType.UINT8)
        in_name = list(ivp.keys())[0]

        per_class_total = defaultdict(int)
        per_class_safe  = defaultdict(int)
        per_class_weapon_scores = defaultdict(list)
        n_skipped_small = 0
        n_total = 0

        with ng.activate(), InferVStreams(ng, ivp, ovp) as p_inf:
            for img_path, mid, name, (xmin_n, ymin_n, xmax_n, ymax_n) in crops:
                img = cv2.imread(str(img_path))
                if img is None:
                    continue
                h, w = img.shape[:2]
                x1 = max(0, int(xmin_n * w))
                y1 = max(0, int(ymin_n * h))
                x2 = min(w, int(xmax_n * w))
                y2 = min(h, int(ymax_n * h))
                if (x2 - x1) < MIN_BOX_PX or (y2 - y1) < MIN_BOX_PX:
                    n_skipped_small += 1
                    continue
                crop = img[y1:y2, x1:x2]
                rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                rgb = cv2.resize(rgb, (224, 224), interpolation=cv2.INTER_LINEAR)
                inp = rgb[np.newaxis].astype(np.uint8)
                raw = p_inf.infer({in_name: inp})
                logits = list(raw.values())[0].astype(np.float32).ravel()
                # HEF outputs UINT8 quantised logits; argmax is order-preserving
                pred_idx = int(np.argmax(logits))
                pred_name = CLASS_NAMES[pred_idx]
                # Safe-confidence proxy: normalised against the larger logit
                weapon_score = float(logits[1]) / max(1.0, float(logits.sum()))

                per_class_total[name] += 1
                if pred_name == "Safe":
                    per_class_safe[name] += 1
                per_class_weapon_scores[name].append(weapon_score)
                n_total += 1

        # All clutter crops are negatives → expected pred = Safe
        # Weapon-precision drop estimator: of all preds, what fraction are weapon (false fires)?
        per_class = {}
        all_safe = 0
        for c in TARGETS.values():
            tot = per_class_total[c]; safe = per_class_safe[c]
            scores = per_class_weapon_scores[c]
            per_class[c] = {
                "n":            tot,
                "n_safe":       safe,
                "n_weapon_FP":  tot - safe,
                "safe_rate":    round(safe / tot, 4) if tot else None,
                "weapon_score_mean": round(float(np.mean(scores)), 4) if scores else None,
            }
            all_safe += safe

        out = {
            "n_total":          n_total,
            "n_safe":           all_safe,
            "n_weapon_FP":      n_total - all_safe,
            "open_set_specificity (P(Safe | clutter))": round(all_safe / n_total, 4) if n_total else None,
            "skipped_small_boxes":   n_skipped_small,
            "min_box_px":            MIN_BOX_PX,
            "per_class":         per_class,
            "hef":                   HEF_PATH,
            "data_source": "Open Images V7 validation, classes: " + ", ".join(TARGETS.values()),
        }
        print("\n=== Open-set MobileNetV2 verifier evaluation ===")
        for k, v in out.items():
            if k == "per_class":
                print("  per_class:")
                for c, d in v.items():
                    print(f"    {c:14s}  n={d['n']:3d}  Safe={d['n_safe']:3d}  weapon_FP={d['n_weapon_FP']:3d}  safe_rate={d['safe_rate']}")
            else:
                print(f"  {k}: {v}")

        OUT_JSON.write_text(json.dumps(out, indent=2))
        print(f"\nWrote {OUT_JSON}")


if __name__ == "__main__":
    main()
