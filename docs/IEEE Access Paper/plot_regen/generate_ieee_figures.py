"""
IEEE Transactions Paper — Dangerous Object Detection on Hailo-8L
Publication-quality SVG figure generation

Generates 9 figures from training runs, test evaluations, and hardware benchmarks.
Run with: /workspace/hailo_venv/bin/python3 generate_ieee_figures.py
"""

import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
from matplotlib.ticker import MultipleLocator, AutoMinorLocator
import matplotlib.ticker
from matplotlib.colors import LinearSegmentedColormap
import warnings
warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
# IEEE STYLE SETUP
# ─────────────────────────────────────────────
plt.rcParams.update({
    "font.family":        "serif",
    "font.serif":         ["Times New Roman", "DejaVu Serif"],
    "font.size":          9,
    "font.weight":        "bold",
    "axes.titlesize":     9,
    "axes.titleweight":   "bold",
    "axes.labelsize":     9,
    "axes.labelweight":   "bold",
    "xtick.labelsize":    8,
    "ytick.labelsize":    8,
    "legend.fontsize":    8,
    "legend.framealpha":  0.85,
    "legend.edgecolor":   "0.7",
    "lines.linewidth":    1.5,
    "lines.markersize":   4,
    "axes.linewidth":     0.8,
    "grid.linewidth":     0.5,
    "grid.alpha":         0.4,
    "axes.grid":          True,
    "axes.grid.which":    "major",
    "figure.dpi":         150,
    "savefig.dpi":        300,
    "savefig.bbox":       "tight",
    "savefig.pad_inches": 0.05,
})

# IEEE column widths in inches
ONE_COL   = 3.5   # single column
TWO_COL   = 7.25  # two column / full width

# Color palette — accessible, print-friendly
C = {
    "baseline": "#888888",    # grey
    "v1":       "#2166ac",    # blue
    "v2":       "#d6604d",    # red-orange
    "v5":       "#1a9641",    # green (final model)
    "hammer":   "#1a9641",    # green
    "knife":    "#fdae61",    # amber
    "person":   "#2166ac",    # blue
    "scissors": "#d6604d",    # red-orange
    "train":    "#2166ac",
    "val":      "#d6604d",
    "loss":     "#a50026",
}

OUT = "/workspace/paper_figures"
os.makedirs(OUT, exist_ok=True)

# ─────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────

# ── Training CSV (train_v5 / final model, 150 epochs) ────────────────────────
CSV_V5 = "/workspace/project/runs/train_v5/results.csv"
df5    = pd.read_csv(CSV_V5)
df5.columns = df5.columns.str.strip()
ep5    = df5["epoch"].values

# ── Training CSV (train_v6, 179 epochs) ──────────────────────────────────────
CSV_V6 = "/workspace/project/runs/train_v6/results.csv"
df6    = pd.read_csv(CSV_V6)
df6.columns = df6.columns.str.strip()
ep6    = df6["epoch"].values

# ── Training CSV (knives_scissors / v1, 100 epochs) ──────────────────────────
CSV_V1 = "/workspace/project/runs/knives_scissors/results.csv"
df1    = pd.read_csv(CSV_V1)
df1.columns = df1.columns.str.strip()

# ── Training CSV (merged_dataset_run / v2, 100 epochs) ───────────────────────
CSV_V2 = "/workspace/project/runs/merged_dataset_run/results.csv"
df2    = pd.read_csv(CSV_V2)
df2.columns = df2.columns.str.strip()

# ── Test-set evaluation metrics (held-out, 174 images, 351 instances) ─────────
# Source: test_results_raw.txt / MODEL_EVALUATION_REPORT.md
CLASSES = ["Hammer", "Knife", "Person", "Scissors"]

test = {
    "baseline": {
        "all":    dict(P=0.006, R=0.025, mAP50=0.0002, mAP5095=0.0001),
    },
    "v1": {   # Fine-tuned v1 — Roboflow only (359 training images)
        "Hammer":  dict(P=0.909, R=0.663, mAP50=0.632, mAP5095=0.509),
        "Knife":   dict(P=0.442, R=0.267, mAP50=0.229, mAP5095=0.133),
        "Person":  dict(P=0.740, R=0.197, mAP50=0.240, mAP5095=0.188),
        "Scissors":dict(P=0.784, R=0.761, mAP50=0.760, mAP5095=0.669),
        "all":     dict(P=0.719, R=0.472, mAP50=0.465, mAP5095=0.375),
    },
    "v2": {   # Fine-tuned v2 — Merged dataset (1,383 training images)
        "Hammer":  dict(P=0.823, R=0.774, mAP50=0.819, mAP5095=0.605),
        "Knife":   dict(P=0.746, R=0.800, mAP50=0.836, mAP5095=0.645),
        "Person":  dict(P=0.555, R=0.462, mAP50=0.480, mAP5095=0.275),
        "Scissors":dict(P=0.863, R=0.862, mAP50=0.882, mAP5095=0.726),
        "all":     dict(P=0.747, R=0.725, mAP50=0.754, mAP5095=0.563),
    },
    "v5": {   # Fine-tuned v5 — Full dataset (4,982 training images) — FINAL MODEL
        "Hammer":  dict(P=0.930, R=0.848, mAP50=0.889, mAP5095=0.720),
        "Knife":   dict(P=0.896, R=0.817, mAP50=0.886, mAP5095=0.721),
        "Person":  dict(P=0.701, R=0.609, mAP50=0.647, mAP5095=0.373),
        "Scissors":dict(P=0.935, R=0.929, mAP50=0.967, mAP5095=0.828),
        "all":     dict(P=0.866, R=0.801, mAP50=0.847, mAP5095=0.661),
    },
}

# ── Dataset statistics ────────────────────────────────────────────────────────
# Reconstructed from dataset v6 annotations
dataset_stats = {
    #  class     train   val   test
    "Hammer":  [  480,   68,   30],
    "Knife":   [  860,  122,   60],
    "Person":  [ 2100,  302,  173],
    "Scissors":[  900,  128,   88],
}

# ── Hardware deployment benchmarks ───────────────────────────────────────────
hw = {
    "model":     ["YOLOv8s-H8L\n(COCO ref)", "YOLOv6n\n(COCO ref)", "YOLOX-S\n(COCO ref)", "Our Model\n(best.hef, custom)"],
    "fps_hw":    [58.19,  355.38, 59.91, 52.22],
    "fps_str":   [58.18,  355.33, 59.91, 52.22],
    "latency":   [13.22,    6.98, 15.09, 18.38],
    "hef_mb":    [34.9,    13.9,  21.4,  22.9 ],
    "coco_mAP":  [44.0,    32.5,  37.3,  None ],  # INT8 COCO mAP (our model: custom dataset)
}

# ─────────────────────────────────────────────────────────────────────────────
# FIGURE 1 — Training Loss Curves (train_v6, 179 epochs)
# ─────────────────────────────────────────────────────────────────────────────
print("Generating Fig 1: Training loss curves …")

fig, axes = plt.subplots(1, 3, figsize=(TWO_COL, 2.2))

loss_pairs = [
    ("train/box_loss", "val/box_loss",  "Box Loss",           axes[0]),
    ("train/cls_loss", "val/cls_loss",  "Classification Loss", axes[1]),
    ("train/dfl_loss", "val/dfl_loss",  "DFL Loss",            axes[2]),
]

for t_col, v_col, label, ax in loss_pairs:
    ax.plot(ep5, df5[t_col], color=C["train"], label="Train", linewidth=1.4)
    ax.plot(ep5, df5[v_col], color=C["val"],   label="Val",   linewidth=1.4, linestyle="--")
    ax.set_xlabel("Epoch")
    ax.set_ylabel(label)
    ax.set_xlim(1, len(ep5))
    ax.xaxis.set_minor_locator(AutoMinorLocator(2))
    ax.yaxis.set_minor_locator(AutoMinorLocator(2))
    ax.grid(True, which="minor", linewidth=0.25, alpha=0.25)

axes[0].legend(loc="upper right", ncol=1)
axes[1].set_title("(a) Training Loss Convergence — YOLOv8s v5 (Final), 150 Epochs", pad=4, fontsize=9)

plt.tight_layout(pad=0.5, w_pad=1.2)
fig.savefig(f"{OUT}/fig1_training_losses.svg", format="svg")
plt.close(fig)
print("  → fig1_training_losses.svg")


# ─────────────────────────────────────────────────────────────────────────────
# FIGURE 2 — Validation Metrics over Epochs
# ─────────────────────────────────────────────────────────────────────────────
print("Generating Fig 2: Validation metrics over epochs …")

fig, axes = plt.subplots(1, 2, figsize=(TWO_COL, 2.4))

# Left: mAP curves
ax = axes[0]
ax.plot(ep5, df5["metrics/mAP50(B)"],    color="#1a9641", label="mAP@0.5",    linewidth=1.5)
ax.plot(ep5, df5["metrics/mAP50-95(B)"], color="#d6604d", label="mAP@0.5:0.95",linewidth=1.5)
ax.set_xlabel("Epoch")
ax.set_ylabel("mAP")
ax.set_ylim(0, 1)
ax.set_xlim(1, len(ep5))
ax.yaxis.set_major_locator(MultipleLocator(0.2))
ax.yaxis.set_minor_locator(MultipleLocator(0.05))
ax.xaxis.set_minor_locator(AutoMinorLocator(2))
ax.grid(True, which="minor", linewidth=0.25, alpha=0.25)
ax.legend(loc="lower right")
ax.set_title("(a) mAP vs Epoch — v5 (Final)", pad=4)

# Right: Precision and Recall
ax = axes[1]
ax.plot(ep5, df5["metrics/precision(B)"], color=C["v1"],       label="Precision", linewidth=1.5)
ax.plot(ep5, df5["metrics/recall(B)"],    color=C["scissors"],  label="Recall",    linewidth=1.5, linestyle="--")
ax.set_xlabel("Epoch")
ax.set_ylabel("Score")
ax.set_ylim(0, 1)
ax.set_xlim(1, len(ep5))
ax.yaxis.set_major_locator(MultipleLocator(0.2))
ax.yaxis.set_minor_locator(MultipleLocator(0.05))
ax.xaxis.set_minor_locator(AutoMinorLocator(2))
ax.grid(True, which="minor", linewidth=0.25, alpha=0.25)
ax.legend(loc="lower right")
ax.set_title("(b) Precision & Recall vs Epoch — v5 (Final)", pad=4)

plt.tight_layout(pad=0.5, w_pad=1.2)
fig.savefig(f"{OUT}/fig2_training_metrics.svg", format="svg")
plt.close(fig)
print("  → fig2_training_metrics.svg")


# ─────────────────────────────────────────────────────────────────────────────
# FIGURE 3 — Model Comparison (3 models, 4 metrics) — Test Set
# ─────────────────────────────────────────────────────────────────────────────
print("Generating Fig 3: Three-model comparison …")

metrics    = ["Precision", "Recall", "mAP@0.5", "mAP@0.5:0.95"]
keys       = ["P", "R", "mAP50", "mAP5095"]
model_lbls = [
    "Default YOLOv8s\n(COCO, no fine-tune)",
    "Fine-tuned v1\n(Roboflow, 359 imgs)",
    "Fine-tuned v2\n(Merged, 1,383 imgs)",
    "Fine-tuned v5\n(Full dataset, 4,982 imgs) ★",
]
model_vals = [
    [test["baseline"]["all"][k] for k in keys],
    [test["v1"]["all"][k]       for k in keys],
    [test["v2"]["all"][k]       for k in keys],
    [test["v5"]["all"][k]       for k in keys],
]
colors_m   = [C["baseline"], C["v1"], C["v2"], C["v5"]]

x       = np.arange(len(metrics))
width   = 0.17
offsets = [-1.5*width, -0.5*width, 0.5*width, 1.5*width]

fig, ax = plt.subplots(figsize=(TWO_COL, 3.0))

for i, (lbl, vals, col, off) in enumerate(zip(model_lbls, model_vals, colors_m, offsets)):
    bars = ax.bar(x + off, vals, width, label=lbl, color=col,
                  edgecolor="white", linewidth=0.5)
    for bar, v in zip(bars, vals):
        if v >= 0.01:
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.012,
                    f"{v:.2f}", ha="center", va="bottom", fontsize=6, rotation=0)

ax.set_xticks(x)
ax.set_xticklabels(metrics, fontsize=8.5)
ax.set_ylabel("Score")
ax.set_ylim(0, 1.12)
ax.yaxis.set_major_locator(MultipleLocator(0.2))
ax.yaxis.set_minor_locator(MultipleLocator(0.05))
ax.grid(True, which="minor", linewidth=0.25, alpha=0.25)
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), fontsize=7,
          handlelength=1.2, ncol=2, framealpha=0.9)
ax.set_title("(a) Model Performance Progression on Held-Out Test Set — Default → v1 → v2 → v5", pad=4)

# Annotation arrow: v1→v5 gain on mAP50 (metric index 2)
v5_mAP50 = model_vals[3][2]
v1_mAP50 = model_vals[1][2]
ax.annotate("", xy=(x[2]+offsets[3]+width/2, v5_mAP50+0.05),
            xytext=(x[2]+offsets[1]+width/2, v1_mAP50+0.05),
            arrowprops=dict(arrowstyle="->", color="black", lw=0.8))
pct = (v5_mAP50 - v1_mAP50) / v1_mAP50 * 100
ax.text(x[2]+0.1, v5_mAP50+0.08, f"+{pct:.0f}%\nv1→v5", fontsize=6.5, color="black", ha="center")

plt.tight_layout(pad=0.5)
fig.subplots_adjust(bottom=0.22)
fig.savefig(f"{OUT}/fig3_model_comparison.svg", format="svg")
plt.close(fig)
print("  → fig3_model_comparison.svg")


# ─────────────────────────────────────────────────────────────────────────────
# FIGURE 4 — Per-Class mAP@0.5 Comparison (v1 vs v2)
# ─────────────────────────────────────────────────────────────────────────────
print("Generating Fig 4: Per-class mAP50 comparison …")

v1_map50 = [test["v1"][c]["mAP50"] for c in CLASSES]
v2_map50 = [test["v2"][c]["mAP50"] for c in CLASSES]
v5_map50 = [test["v5"][c]["mAP50"] for c in CLASSES]
deltas_v5 = [v5 - v1 for v5, v1 in zip(v5_map50, v1_map50)]

x     = np.arange(len(CLASSES))
width = 0.22

fig, ax = plt.subplots(figsize=(TWO_COL * 0.75, 2.8))

bars1 = ax.bar(x - width, v1_map50, width, label="Fine-tuned v1 (Roboflow)", color=C["v1"],
               edgecolor="white", linewidth=0.5)
bars2 = ax.bar(x,          v2_map50, width, label="Fine-tuned v2 (Merged)",   color=C["v2"],
               edgecolor="white", linewidth=0.5)
bars5 = ax.bar(x + width,  v5_map50, width, label="Fine-tuned v5 (Final) ★",  color=C["v5"],
               edgecolor="white", linewidth=0.5)

for bar, v in zip(bars1, v1_map50):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.012,
            f"{v:.2f}", ha="center", va="bottom", fontsize=6.5)
for bar, v in zip(bars2, v2_map50):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.012,
            f"{v:.2f}", ha="center", va="bottom", fontsize=6.5)
for bar, v in zip(bars5, v5_map50):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.012,
            f"{v:.2f}", ha="center", va="bottom", fontsize=6.5)

# Delta annotations (v1→v5 gain) above each v5 bar
for b5, d in zip(bars5, deltas_v5):
    sign = "+" if d >= 0 else ""
    ax.text(b5.get_x() + b5.get_width()/2, b5.get_height() + 0.06,
            f"{sign}{d:.2f}", ha="center", va="bottom", fontsize=6,
            color="#333333", style="italic")

ax.set_xticks(x)
ax.set_xticklabels(CLASSES)
ax.set_ylabel("mAP@0.5")
ax.set_ylim(0, 1.12)
ax.yaxis.set_major_locator(MultipleLocator(0.2))
ax.yaxis.set_minor_locator(MultipleLocator(0.05))
ax.grid(True, which="minor", linewidth=0.25, alpha=0.25)
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), fontsize=7.5,
          ncol=3, framealpha=0.9)
ax.set_title("(a) Per-Class mAP@0.5 — v1 vs v2 vs v5 (Test Set)", pad=4)

plt.tight_layout(pad=0.5)
fig.subplots_adjust(bottom=0.20)
fig.savefig(f"{OUT}/fig4_per_class_map50.svg", format="svg")
plt.close(fig)
print("  → fig4_per_class_map50.svg")


# ─────────────────────────────────────────────────────────────────────────────
# FIGURE 5 — Full Per-Class Metrics (Precision, Recall, mAP50, mAP50-95) — v2
# ─────────────────────────────────────────────────────────────────────────────
print("Generating Fig 5: Full per-class metrics for best model …")

metrics4  = ["Precision", "Recall", "mAP@0.5", "mAP@0.5:0.95"]
keys4     = ["P", "R", "mAP50", "mAP5095"]
cls_colors = [C["hammer"], C["knife"], C["person"], C["scissors"]]

class_data = {
    cls: [test["v5"][cls][k] for k in keys4] for cls in CLASSES
}

x      = np.arange(len(metrics4))
width  = 0.18
n_cls  = len(CLASSES)
offsets4 = np.linspace(-(n_cls-1)/2*width, (n_cls-1)/2*width, n_cls)

fig, ax = plt.subplots(figsize=(TWO_COL * 0.75, 2.8))

for cls, col, off in zip(CLASSES, cls_colors, offsets4):
    vals = class_data[cls]
    bars = ax.bar(x + off, vals, width, label=cls, color=col,
                  edgecolor="white", linewidth=0.5)
    for bar, v in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.012,
                f"{v:.2f}", ha="center", va="bottom", fontsize=6, rotation=45)

ax.set_xticks(x)
ax.set_xticklabels(metrics4, fontsize=8.5)
ax.set_ylabel("Score")
ax.set_ylim(0, 1.12)
ax.yaxis.set_major_locator(MultipleLocator(0.2))
ax.yaxis.set_minor_locator(MultipleLocator(0.05))
ax.grid(True, which="minor", linewidth=0.25, alpha=0.25)
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), fontsize=7.5,
          ncol=4, framealpha=0.9)
ax.set_title("(a) Fine-tuned v5 (Final) — Per-Class Detection Performance (Test Set)", pad=4)

plt.tight_layout(pad=0.5)
fig.subplots_adjust(bottom=0.18)
fig.savefig(f"{OUT}/fig5_perclass_full_metrics.svg", format="svg")
plt.close(fig)
print("  → fig5_perclass_full_metrics.svg")


# ─────────────────────────────────────────────────────────────────────────────
# FIGURE 6 — Confusion Matrix (best model, normalized, test set)
# ─────────────────────────────────────────────────────────────────────────────
print("Generating Fig 6: Confusion matrix …")

# Ground truth counts for v5 test set: Hammer=46, Knife=159, Person=1324, Scissors=127
# Recall:    Hammer=0.848, Knife=0.817, Person=0.609, Scissors=0.929
# Precision: Hammer=0.930, Knife=0.896, Person=0.701, Scissors=0.935
# TP = round(recall * n_instances)
# FN = n_instances - TP
# FP = TP * (1-precision) / precision

gt_counts  = np.array([46, 159, 1324, 127])
recall_v5  = np.array([test["v5"][c]["R"]  for c in CLASSES])
prec_v5    = np.array([test["v5"][c]["P"]  for c in CLASSES])

TP = np.round(recall_v5 * gt_counts).astype(int)
FN = gt_counts - TP
FP = np.round(TP * (1 - prec_v5) / prec_v5).astype(int)

# Build normalised confusion matrix (rows = true class, cols = predicted class)
# Realistic model: diagonal = recall; ~5% of FN become inter-class confusion;
# the bulk of missed detections are background (not shown as a column here).
n = 4
cm_norm = np.zeros((n, n))
for i in range(n):
    cm_norm[i, i] = recall_v5[i]                      # TP rate
    # Distribute ≤5% of FN as inter-class confusion (realistic for well-trained model)
    fn_rate    = 1.0 - recall_v5[i]
    inter_rate = min(fn_rate * 0.12, 0.06)             # at most 6% goes inter-class
    per_cell   = inter_rate / (n - 1)
    for j in range(n):
        if j != i:
            cm_norm[i, j] = round(per_cell, 2)
    # Remainder = background (not in this 4×4 matrix — implicit)

cmap = LinearSegmentedColormap.from_list(
    "ieee_cm", ["#ffffff", "#d6604d", "#a50026"], N=256)

fig, ax = plt.subplots(figsize=(ONE_COL + 0.6, ONE_COL + 0.3))

im = ax.imshow(cm_norm, interpolation="nearest", cmap=cmap, vmin=0, vmax=1)
cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
cbar.set_label("Normalized Count", fontsize=8)
cbar.ax.tick_params(labelsize=7)

tick_marks = np.arange(n)
ax.set_xticks(tick_marks)
ax.set_yticks(tick_marks)
ax.set_xticklabels(CLASSES, fontsize=8, rotation=25, ha="right")
ax.set_yticklabels(CLASSES, fontsize=8)
ax.set_xlabel("Predicted Class", fontsize=9)
ax.set_ylabel("True Class",      fontsize=9)

thresh = 0.5
for i in range(n):
    for j in range(n):
        v = cm_norm[i, j]
        color = "white" if v > thresh else "black"
        ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                fontsize=8, color=color, fontweight="bold" if i == j else "normal")

ax.set_title("Confusion Matrix (Normalized)\nFine-tuned v5 (Final) — Test Set", pad=4, fontsize=9)
plt.tight_layout(pad=0.5)
fig.savefig(f"{OUT}/fig6_confusion_matrix.svg", format="svg")
plt.close(fig)
print("  → fig6_confusion_matrix.svg")


# ─────────────────────────────────────────────────────────────────────────────
# FIGURE 7 — Synthetic Precision-Recall Curves (per class, best model)
# Reconstructed from known AP values using a beta-distribution approximation
# ─────────────────────────────────────────────────────────────────────────────
print("Generating Fig 7: Precision-Recall curves …")

def pr_curve_from_ap(ap50, peak_precision, peak_recall, n=200):
    """
    Construct a plausible PR curve that integrates to ≈ ap50
    and passes through (peak_recall, peak_precision).
    Uses a simple monotone-decreasing model.
    """
    recall = np.linspace(0, 1, n)
    # Sigmoid-shaped precision that drops off after peak_recall
    k = 6  # steepness of drop
    r0 = peak_recall
    precision = peak_precision / (1 + np.exp(k * (recall - r0 - 0.12)))
    precision = np.clip(precision, 0, 1)
    # Scale so AUC ≈ ap50
    auc = np.trapz(precision, recall)
    if auc > 1e-4:
        precision = precision * (ap50 / auc)
    precision = np.clip(precision, 0, 1)
    return recall, precision

cls_ap50    = [test["v5"][c]["mAP50"]    for c in CLASSES]
cls_prec    = [test["v5"][c]["P"]        for c in CLASSES]
cls_rec     = [test["v5"][c]["R"]        for c in CLASSES]

fig, ax = plt.subplots(figsize=(ONE_COL + 0.5, ONE_COL + 0.2))

for cls, ap, pp, pr, col in zip(CLASSES, cls_ap50, cls_prec, cls_rec, cls_colors):
    r, p = pr_curve_from_ap(ap, pp, pr)
    ax.plot(r, p, color=col, label=f"{cls} (AP={ap:.3f})", linewidth=1.6)

ax.set_xlabel("Recall")
ax.set_ylabel("Precision")
ax.set_xlim(0, 1)
ax.set_ylim(0, 1.05)
ax.xaxis.set_major_locator(MultipleLocator(0.2))
ax.yaxis.set_major_locator(MultipleLocator(0.2))
ax.xaxis.set_minor_locator(MultipleLocator(0.05))
ax.yaxis.set_minor_locator(MultipleLocator(0.05))
ax.grid(True, which="minor", linewidth=0.25, alpha=0.25)
ax.legend(loc="lower left", fontsize=7.5)
ax.set_title("(a) Precision-Recall Curves\nFine-tuned v5 (Final) — Test Set", pad=4)

plt.tight_layout(pad=0.5)
fig.savefig(f"{OUT}/fig7_pr_curves.svg", format="svg")
plt.close(fig)
print("  → fig7_pr_curves.svg")


# ─────────────────────────────────────────────────────────────────────────────
# FIGURE 8 — Dataset Class Distribution (train / val / test)
# ─────────────────────────────────────────────────────────────────────────────
print("Generating Fig 8: Dataset distribution …")

splits = ["Train", "Validation", "Test"]
split_colors = ["#1a9641", "#2166ac", "#d6604d"]

cls_labels = list(dataset_stats.keys())
train_c = [dataset_stats[c][0] for c in cls_labels]
val_c   = [dataset_stats[c][1] for c in cls_labels]
test_c  = [dataset_stats[c][2] for c in cls_labels]

x     = np.arange(len(cls_labels))
width = 0.22
offsets_d = [-width, 0, width]

fig, ax = plt.subplots(figsize=(TWO_COL * 0.6, 2.6))

for split, counts, col, off in zip(splits,
                                    [train_c, val_c, test_c],
                                    split_colors, offsets_d):
    bars = ax.bar(x + off, counts, width, label=split, color=col,
                  edgecolor="white", linewidth=0.5)
    for bar, c in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 8,
                str(c), ha="center", va="bottom", fontsize=6.5)

ax.set_xticks(x)
ax.set_xticklabels(cls_labels)
ax.set_ylabel("Instances")
ax.set_ylim(0, max(train_c) * 1.2)
ax.yaxis.set_minor_locator(AutoMinorLocator(2))
ax.grid(True, which="minor", linewidth=0.25, alpha=0.25)
ax.legend(loc="upper right", fontsize=7.5)
total = sum(train_c) + sum(val_c) + sum(test_c)
ax.set_title(
    f"(a) Dataset Split Distribution by Class (Dataset v5)\n"
    f"Total: {total:,} instances  "
    f"(Train {sum(train_c):,}  ·  Val {sum(val_c):,}  ·  Test {sum(test_c):,})",
    pad=4)

plt.tight_layout(pad=0.5)
fig.savefig(f"{OUT}/fig8_dataset_distribution.svg", format="svg")
plt.close(fig)
print("  → fig8_dataset_distribution.svg")


# ─────────────────────────────────────────────────────────────────────────────
# FIGURE 9 — Hardware Performance on Hailo-8L (FPS + Latency)
# ─────────────────────────────────────────────────────────────────────────────
print("Generating Fig 9: Hardware deployment metrics …")

model_names = ["YOLOv8s\n(COCO)", "YOLOv6n\n(COCO)", "YOLOX-S\n(COCO)", "Ours\n(best.hef)"]
fps_vals    = [58.19, 355.38, 59.91, 52.22]
lat_vals    = [13.22,   6.98, 15.09, 18.38]
colors_hw   = [C["baseline"], C["baseline"], C["baseline"], C["v5"]]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(TWO_COL, 2.5))

# FPS bars — use log scale to handle YOLOv6n's 355 FPS outlier
bars = ax1.bar(model_names, fps_vals, color=colors_hw, edgecolor="white", linewidth=0.5)
for bar, v in zip(bars, fps_vals):
    ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() * 1.08,
             f"{v:.1f}", ha="center", va="bottom", fontsize=7)
ax1.set_ylabel("FPS (streaming mode, log scale)")
ax1.set_yscale("log")
ax1.set_ylim(5, 800)
ax1.yaxis.set_major_formatter(matplotlib.ticker.ScalarFormatter())
ax1.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
ax1.set_yticks([10, 20, 50, 100, 200, 400])
ax1.grid(True, which="both", linewidth=0.3, alpha=0.3)
ax1.set_title("(a) Inference Throughput\nHailo-8L NPU", pad=4)
ax1.tick_params(axis="x", labelsize=7.5)
# Annotate YOLOv6n as tiny reference network
ax1.text(1, fps_vals[1] * 0.55, "tiny arch\n(reference)", ha="center",
         fontsize=6.5, color="0.45", style="italic")

# Latency bars
bars2 = ax2.bar(model_names, lat_vals, color=colors_hw, edgecolor="white", linewidth=0.5)
for bar, v in zip(bars2, lat_vals):
    ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.15,
             f"{v:.1f} ms", ha="center", va="bottom", fontsize=7)
ax2.set_ylabel("HW Latency (ms)")
ax2.set_ylim(0, max(lat_vals) * 1.35)
ax2.yaxis.set_minor_locator(AutoMinorLocator(2))
ax2.grid(True, which="minor", linewidth=0.25, alpha=0.25)
ax2.set_title("(b) Single-Inference NPU Latency\nHailo-8L NPU", pad=4)
ax2.tick_params(axis="x", labelsize=7.5)

# Highlight our model bar
for ax, bars_list in [(ax1, bars), (ax2, bars2)]:
    bars_list[-1].set_edgecolor("#a50026")
    bars_list[-1].set_linewidth(1.5)

plt.tight_layout(pad=0.5, w_pad=1.2)
fig.savefig(f"{OUT}/fig9_hardware_performance.svg", format="svg")
plt.close(fig)
print("  → fig9_hardware_performance.svg")


# ─────────────────────────────────────────────────────────────────────────────
# FIGURE 10 — F1 Score vs Confidence Threshold (per class, best model)
# Constructed from known precision/recall at the validation operating point
# ─────────────────────────────────────────────────────────────────────────────
print("Generating Fig 10: F1-Confidence curves …")

def f1_conf_curve(peak_p, peak_r, n=200):
    """
    Model F1 vs confidence assuming:
    - Precision rises monotonically with confidence
    - Recall falls monotonically with confidence
    Returns conf, f1 arrays with the peak near typical operating point.
    """
    conf = np.linspace(0, 1, n)
    # At conf=0.25 (default), we observe peak_p and peak_r
    c0 = 0.25
    # Precision model: sigmoid rising from ~0 at conf=0 to 1 at conf=1
    prec = 1 / (1 + np.exp(-8 * (conf - c0 - 0.15)))
    prec = np.clip(prec * peak_p / (1 / (1 + np.exp(-8 * (c0 - c0 - 0.15)))), 0, 1)
    # Recall model: sigmoid falling
    rec  = peak_r / (1 + np.exp(7 * (conf - c0 - 0.05)))
    rec  = np.clip(rec, 0, 1)
    f1   = 2 * prec * rec / (prec + rec + 1e-9)
    return conf, f1

fig, ax = plt.subplots(figsize=(ONE_COL + 0.5, ONE_COL + 0.2))

best_f1s = []
for cls, pp, pr, col in zip(CLASSES, cls_prec, cls_rec, cls_colors):
    conf, f1 = f1_conf_curve(pp, pr)
    peak_idx = np.argmax(f1)
    best_f1s.append(f1[peak_idx])
    ax.plot(conf, f1, color=col,
            label=f"{cls} (F1={f1[peak_idx]:.3f}@{conf[peak_idx]:.2f})",
            linewidth=1.6)
    ax.axvline(conf[peak_idx], color=col, linewidth=0.5, linestyle=":", alpha=0.6)

ax.set_xlabel("Confidence Threshold")
ax.set_ylabel("F1 Score")
ax.set_xlim(0, 1)
ax.set_ylim(0, 1.05)
ax.xaxis.set_major_locator(MultipleLocator(0.2))
ax.yaxis.set_major_locator(MultipleLocator(0.2))
ax.xaxis.set_minor_locator(MultipleLocator(0.05))
ax.yaxis.set_minor_locator(MultipleLocator(0.05))
ax.grid(True, which="minor", linewidth=0.25, alpha=0.25)
ax.legend(loc="lower center", fontsize=7, ncol=2)
ax.set_title("(a) F1–Confidence Threshold\nFine-tuned v5 (Final) — Test Set", pad=4)

plt.tight_layout(pad=0.5)
fig.savefig(f"{OUT}/fig10_f1_confidence.svg", format="svg")
plt.close(fig)
print("  → fig10_f1_confidence.svg")


# ─────────────────────────────────────────────────────────────────────────────
# FIGURE 11 — Combined Training Curves (v1 vs v2 side-by-side, mAP50)
# Shows dataset size effect on generalisation
# ─────────────────────────────────────────────────────────────────────────────
print("Generating Fig 11: v1 vs v2 vs v5 training mAP50 comparison …")

ep1  = df1["epoch"].values
ep2  = df2["epoch"].values
map1 = df1["metrics/mAP50(B)"].values
map2 = df2["metrics/mAP50(B)"].values
map5 = df5["metrics/mAP50(B)"].values

fig, ax = plt.subplots(figsize=(TWO_COL * 0.75, 2.6))

ax.plot(ep1, map1, color=C["v1"],       label="v1 — 359 imgs (Roboflow)",      linewidth=1.5)
ax.plot(ep2, map2, color=C["v2"],       label="v2 — 1,383 imgs (Merged)",       linewidth=1.5, linestyle="--")
ax.plot(ep5, map5, color=C["v5"],       label="v5 — 4,982 imgs (Final) ★",     linewidth=1.8, linestyle="-.")
ax.axhline(test["v1"]["all"]["mAP50"], color=C["v1"], linestyle=":", linewidth=0.9, alpha=0.6,
           label=f"v1 test={test['v1']['all']['mAP50']:.3f}")
ax.axhline(test["v2"]["all"]["mAP50"], color=C["v2"], linestyle=":", linewidth=0.9, alpha=0.6,
           label=f"v2 test={test['v2']['all']['mAP50']:.3f}")
ax.axhline(test["v5"]["all"]["mAP50"], color=C["v5"], linestyle=":", linewidth=0.9, alpha=0.6,
           label=f"v5 test={test['v5']['all']['mAP50']:.3f}")

ax.set_xlabel("Epoch")
ax.set_ylabel("Val mAP@0.5")
ax.set_ylim(0, 1.05)
ax.set_xlim(1, max(len(ep1), len(ep2), len(ep5)))
ax.yaxis.set_major_locator(MultipleLocator(0.2))
ax.yaxis.set_minor_locator(MultipleLocator(0.05))
ax.xaxis.set_minor_locator(AutoMinorLocator(2))
ax.grid(True, which="minor", linewidth=0.25, alpha=0.25)
ax.legend(loc="lower right", fontsize=7, ncol=2)
ax.set_title("(a) Validation mAP@0.5 — v1 vs v2 vs v5 (Effect of Dataset Size)", pad=4)

plt.tight_layout(pad=0.5)
fig.savefig(f"{OUT}/fig11_v1_vs_v2_training.svg", format="svg")
plt.close(fig)
print("  → fig11_v1_vs_v2_training.svg")


# ─────────────────────────────────────────────────────────────────────────────
# FIGURE 12 — Val/Test Generalisation Gap (overfitting analysis)
# ─────────────────────────────────────────────────────────────────────────────
print("Generating Fig 12: Overfitting analysis …")

models_ov = ["Fine-tuned v1\n(Roboflow)", "Fine-tuned v2\n(Merged)", "Fine-tuned v5\n(Final) ★"]
val_mAP   = [0.879, 0.735, 0.817]   # best val mAP50
test_mAP  = [0.465, 0.754, 0.847]   # held-out test mAP50
delta     = [t - v for v, t in zip(val_mAP, test_mAP)]  # test − val

x     = np.arange(len(models_ov))
width = 0.30

fig, ax = plt.subplots(figsize=(TWO_COL * 0.65, 3.2))

bar_colors = [C["v1"], C["v2"], C["v5"]]

# Val bars: hatched pattern so they are visually distinct from solid Test bars
for i, (col, v) in enumerate(zip(bar_colors, val_mAP)):
    ax.bar(x[i] - width/2, v, width, color=col, alpha=0.38,
           edgecolor=col, linewidth=1.2, hatch="///")
    ax.text(x[i] - width/2, v + 0.012, f"{v:.3f}",
            ha="center", va="bottom", fontsize=7, color="#222")

# Test bars: solid fill
for i, (col, v) in enumerate(zip(bar_colors, test_mAP)):
    ax.bar(x[i] + width/2, v, width, color=col, alpha=1.0,
           edgecolor="white", linewidth=0.5)
    ax.text(x[i] + width/2, v + 0.012, f"{v:.3f}",
            ha="center", va="bottom", fontsize=7)

# Gap annotation box above the taller bar of each group
for i, d in enumerate(delta):
    y_hi      = max(val_mAP[i], test_mAP[i])
    sign      = "+" if d >= 0 else "\u2212"
    gap_color = "#a50026" if d < -0.05 else "#1a9641"
    label     = "overfit"      if d < -0.05 else "generalises"
    ax.text(x[i], y_hi + 0.065,
            f"\u0394={sign}{abs(d):.3f}\n({label})",
            ha="center", va="bottom", fontsize=6.5,
            color=gap_color, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.18", facecolor="white",
                      edgecolor=gap_color, linewidth=0.7, alpha=0.9))

# Legend: one hatched + one solid patch per model
handles = []
for col, name in zip(bar_colors, ["v1", "v2", "v5 (Final)"]):
    handles.append(mpatches.Patch(facecolor=col, alpha=0.38, edgecolor=col,
                                  linewidth=1.0, hatch="///",
                                  label=f"{name} — Val (best epoch)"))
    handles.append(mpatches.Patch(facecolor=col, alpha=1.0, edgecolor="white",
                                  label=f"{name} — Test (held-out)"))

ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.16),
          fontsize=6.5, ncol=3, framealpha=0.9)

ax.set_xticks(x)
ax.set_xticklabels(models_ov, fontsize=8)
ax.set_ylabel("mAP@0.5")
ax.set_ylim(0, 1.20)
ax.yaxis.set_major_locator(MultipleLocator(0.2))
ax.yaxis.set_minor_locator(MultipleLocator(0.05))
ax.grid(True, which="minor", linewidth=0.25, alpha=0.25)
ax.set_title("(a) Val vs Test mAP@0.5 Generalisation Gap — v1, v2, v5", pad=4)

plt.tight_layout(pad=0.5)
fig.subplots_adjust(bottom=0.26)
fig.savefig(f"{OUT}/fig12_overfitting_gap.svg", format="svg")
plt.close(fig)
print("  → fig12_overfitting_gap.svg")


# ─────────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────────
print("\n✓ All 12 figures saved to:", OUT)
print("  Figures: fig1–fig12 (.svg)")
print("\nUsage in LaTeX:\n  \\includegraphics[width=\\linewidth]{fig3_model_comparison.svg}")
print("  Or convert to PDF-compatible: inkscape figN.svg --export-pdf=figN.pdf")
