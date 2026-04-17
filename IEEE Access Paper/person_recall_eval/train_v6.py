"""
v6 training — targeting P/R > 0.875 across ALL classes (87.5-95%).

v5 gap analysis:
  Person:   P=0.701, R=0.609  ← sole bottleneck, test set avg 3.2 persons/image
  Hammer:   P=0.930, R=0.848  ← R needs +0.027
  Knife:    P=0.896, R=0.817  ← R needs +0.058
  Scissors: P=0.935, R=0.929  ← already above target

Key upgrades over v5:
  1. +2000 diverse Person images (OI validation split, different seed)
  2. copy_paste=0.5  (up from 0.3 — more aggressive person synthesis)
  3. mixup=0.2       (up from 0.1)
  4. label_smoothing=0.05 — prevents overconfidence, improves generalization
  5. epochs=200      (more time to converge)
  6. patience=75
  7. close_mosaic=20 (longer mosaic — better for crowded scenes)
  8. lrf=0.0001      (lower final LR for cleaner convergence)

Deployment: Hailo-8L (13 TOPS) — yolov8s.pt kept (28.4 GFLOPs, real-time)
"""

from pathlib import Path
from ultralytics import YOLO

PROJECT_DIR = Path(__file__).parent
DATA_YAML   = Path("/workspace/datasets/dataset_v6/data.yaml")

if not DATA_YAML.exists():
    print(f"ERROR: {DATA_YAML} not found. Run build_dataset_v6.py first.")
    exit(1)

model = YOLO("yolov8s.pt")  # fresh COCO pretrained — no v5 fine-tuning (avoids regression)

results = model.train(
    data=str(DATA_YAML),
    epochs=200,
    imgsz=640,
    batch=32,
    device=0,
    workers=4,
    project=str(PROJECT_DIR / "runs"),
    name="train_v6",
    exist_ok=True,
    patience=75,

    # ── Learning rate ──────────────────────────────────────────────────────────
    lr0=0.01,
    lrf=0.0001,          # lower final LR for cleaner final convergence
    cos_lr=True,

    # ── Augmentation — tuned for crowded person scenes ─────────────────────────
    mosaic=1.0,
    close_mosaic=20,     # keep mosaic active 20 more epochs than v5 (crowded scenes)
    fliplr=0.5,
    flipud=0.0,
    hsv_h=0.015,
    hsv_s=0.7,
    hsv_v=0.4,
    scale=0.6,
    translate=0.1,
    degrees=0.0,
    erasing=0.4,
    auto_augment="randaugment",

    copy_paste=0.5,      # up from 0.3 — more synthetic person instances per batch
    mixup=0.2,           # up from 0.1 — stronger regularisation
    shear=2.0,
    perspective=0.0001,

    # ── Loss ───────────────────────────────────────────────────────────────────
    box=7.5,
    cls=0.5,
    dfl=1.5,
    label_smoothing=0.05,  # prevents overconfidence, improves recall on hard cases
)

print("\n=== v6 Training Complete ===")
best = PROJECT_DIR / "runs" / "train_v6" / "weights" / "best.pt"
print(f"Best weights: {best}")

# ── Confidence threshold sweep (recall-focused for security system) ───────────
val_model = YOLO(str(best))
CLASS_NAMES = ["Hammer", "Knife", "Person", "scissors"]

print("\n" + "="*65)
print("Test set evaluation — confidence threshold sweep")
print("="*65)

for conf in [0.25, 0.15, 0.10]:
    m = val_model.val(data=str(DATA_YAML), split="test", device=0, conf=conf, verbose=False)
    f1 = 2*m.box.mp*m.box.mr/(m.box.mp+m.box.mr) if (m.box.mp+m.box.mr) > 0 else 0
    print(f"\n  conf={conf:.2f}  P={m.box.mp:.3f}  R={m.box.mr:.3f}  F1={f1:.3f}  mAP50={m.box.map50:.3f}")
    for i, name in enumerate(CLASS_NAMES):
        try:
            p = m.box.p[i]; r = m.box.r[i]
            f = 2*p*r/(p+r) if (p+r) > 0 else 0
            print(f"    {name:10s}: P={p:.3f}  R={r:.3f}  F1={f:.3f}")
        except Exception:
            pass

print("\n** For Hailo-8L security deployment: use conf=0.15 for max recall **")
print(f"\nBest weights: {best}")
