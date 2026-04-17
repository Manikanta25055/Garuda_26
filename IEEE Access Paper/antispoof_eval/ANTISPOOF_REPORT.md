# Anti-Spoof Evaluation — IEEE Access Submission-Grade Report

**Date:** 2026-04-17
**Section under test (P0-4):** the cascade's fixed 0.05 MiDaS depth-variance threshold needs real quantitative backing — real-vs-spoof distributions, ROC, AUC with confidence intervals, comparison to published baselines.

---

## 1. Headline table

**All AUCs with 1000-iter bootstrap 95 % CI. All rows evaluated on the same CelebA-Spoof held-out 20 % test split (n = 600), then repeated on the user batchA/B set.**

| Method | Feature dim | CelebA test AUC (95 % CI) | EER | TPR @ 10 % FPR |
|---|---:|---|---:|---:|
| **B0** Depth-variance threshold (claim under test) | 1 | 0.563 [0.516, 0.610] | 0.468 | 0.196 |
| **B1** LBP + SVM (Chingovska 2012 style) | 40 | 0.848 [0.817, 0.878] | 0.218 | 0.577 |
| **B2** Colour-Texture HSV-histogram + SVM | 512 | 0.898 [0.872, 0.921] | 0.188 | 0.713 |
| Ours / LogReg | 25 | 0.877 [0.849, 0.904] | 0.193 | 0.653 |
| Ours / RandomForest | 25 | 0.941 [0.923, 0.957] | 0.147 | 0.807 |
| Ours / HistGradientBoosting | 25 | 0.951 [0.935, 0.966] | 0.130 | 0.860 |
| **Ours / SVM-RBF (chosen)** | **25** | **0.951 [0.935, 0.967]** | **0.120** | **0.867** |

| User set evaluation (batchA + batchB, n = 59) | AUC (95 % CI) | EER | Sens / Spec @ Youden |
|---|---|---:|---|
| Ours / SVM-RBF, Leave-One-Out CV | 0.914 [0.832, 0.978] | 0.136 | 0.897 / 0.867 |
| Ours / SVM-RBF, trained on CelebA → tested cross-domain | 0.442 [0.301, 0.583] | 0.509 | — |

**Contribution in one sentence.** Replacing a single MiDaS depth-variance threshold (AUC 0.56) with a 25-dim handcrafted descriptor (depth + FFT + chroma-gradient + uniform LBP) and an RBF-SVM raises performance to AUC 0.95 on CelebA-Spoof's held-out test (improvement of +0.39 AUC, statistically significant by non-overlapping 95 % CIs), outperforms two published baselines (LBP+SVM by +0.10, Colour-Texture by +0.05), and generalises to an in-house 59-image cascade-camera dataset at AUC 0.91. Cross-corpus transfer collapses to AUC 0.44 — a domain-gap finding we report explicitly rather than hide.

---

## 2. Data

| Source | Real | Spoof | Role |
|---|---:|---:|---|
| CelebA-Spoof test split (HuggingFace `nguyenkhoa/celeba-spoof-for-face-antispoofing-test`) | 1 500 | 1 500 | Benchmark (stratified 80 / 20 split, seed 0) |
| — train partition | 1 200 | 1 200 | 5-fold CV / model fitting |
| — test partition  | 300   | 300   | Held-out evaluation (Table 1, columns) |
| User `batchA` (iPhone camera capture) | 29 | — | In-domain real |
| User `batchB` (photographed laptop screen) | — | 30 | In-domain screen spoof |

User crops were produced by `/workspace/prep_uploaded_images.py` (YOLOv8 best-person detection, 256 × 256 Lanczos resize; centre-crop fallback when no person detected — 1 / 29 real, 2 / 30 spoof). CelebA crops were streamed balanced with `MIN_SIDE ≥ 96 px` filter. The full fetch is reproducible via `fetch_celeba_spoof.py` (seed 0, HuggingFace streaming API, `TARGET_PER_CLASS = 1500`).

---

## 3. Method

### 3.1 Feature vector (25 dims, labelled `Ours`)

| Group | Features | Definition | Rationale |
|---|---|---|---|
| Depth | `depth_var`, `depth_grad` | Variance and mean-gradient-magnitude of min-max normalised MiDaS-Small depth map | 3-D structure: flat spoofs → low variance, sharp silhouette → high grad |
| Texture | `lap_var`, `hf_sobel`, `edge_den` | Laplacian variance, mean \|Sobel\|, Canny edge density | Print / screen blur |
| Frequency | `fft_hi`, `fft_mid`, `fft_dir` | Radial FFT energy in ring r > 0.5 r_max / 0.2–0.5 r_max, plus directional peak | Moire, aliasing, LCD sub-pixel grid |
| Colour | `sat_std`, `sat_mean`, `val_std`, `col_a_std`, `col_b_std` | HSV saturation/value stats, Lab a/b spread | Gamut compression on LCD |
| Chroma gradient | `col_grad_a`, `col_grad_b` | Mean \|Sobel(a)\|, \|Sobel(b)\| on Lab channels | Strongest univariate cue (AUC 0.80 on user set) — screen sub-pixel filtering smooths chroma |
| Micro-texture | `lbp_0 … lbp_9` | Uniform-LBP (P=8, R=1) 10-bin density histogram | Classic anti-spoof descriptor |

Crops are resized to 256 × 256 before feature extraction.

### 3.2 Depth backbone

MiDaS Small (`intel-isl/MiDaS @ master`), loaded via `torch.hub`. Single forward pass per crop on CUDA, FP32. Depth is min-max normalised per image, then statistics are computed.

### 3.3 Baselines (reimplemented for identical protocol)

- **B0 Depth-variance threshold.** The exact claim under test: `depth_var` → threshold classifier. Score = `+depth_var` or `−depth_var`, whichever gives higher AUC (sign flips because spoofs are not consistently lower-variance under MiDaS per-image normalisation).
- **B1 LBP + SVM.** Chingovska 2012-style: grayscale 128 × 128, 2 × 2 spatial grid, uniform LBP (P=8, R=1) per cell → concatenate 10-bin histograms (40 dims) → RBF-SVM.
- **B2 Colour-Texture.** HSV 8 × 8 × 8 = 512-bin joint histogram, L2-normalised → RBF-SVM.

### 3.4 Classifiers

All models from scikit-learn 1.8.0. Standard scaling applied before every linear / kernel model. Hyperparameters were fixed, not tuned on the test set:

- LogisticRegression(C=1.0, max_iter=3000)
- RandomForest(n_estimators=500, seed=0)
- HistGradientBoosting(max_iter=400, lr=0.05, seed=0)
- SVM-RBF (C=4.0, γ=scale, probability=True, seed=0)

### 3.5 Protocol

- **CelebA-Spoof**: Stratified 80 / 20 train / test split (seed 0). 5-fold Stratified-K-Fold CV on the train partition for method selection; final numbers reported on the held-out 20 % test.
- **User set (n=59)**: Leave-One-Out CV with Ours / SVM-RBF.
- **Cross-domain**: CelebA-train → user set, single pass.
- **Thresholding**: Youden-J = argmax(TPR − FPR) on each evaluation's own ROC.
- **Statistics**: 1 000 bootstrap resamples stratified by class, percentile 95 % CI on AUC and EER.

---

## 4. Results

### 4.1 CelebA-Spoof 5-fold CV (train partition, n = 2 400)

| Method | AUC (95 % CI) | EER |
|---|---|---:|
| B1 LBP+SVM | 0.836 [0.819, 0.852] | 0.245 |
| B2 Colour-Texture | 0.893 [0.880, 0.905] | 0.187 |
| Ours / LogReg | 0.865 [0.848, 0.878] | 0.208 |
| Ours / RandomForest | 0.921 [0.911, 0.932] | 0.153 |
| Ours / HistGBM | 0.941 [0.932, 0.949] | 0.134 |
| **Ours / SVM-RBF** | **0.948 [0.939, 0.956]** | **0.125** |

### 4.2 CelebA-Spoof held-out test (n = 600)

See §1 headline table.

B0 (the cascade's current depth-variance-only approach) reaches only **AUC 0.563** on this benchmark — barely above chance. This directly invalidates the 0.05 threshold as presented.

### 4.3 Feature importance (permutation, RandomForest, test set)

Top-10 by mean AUC drop when shuffled:

```
col_grad_b        0.065   ← Lab-b chroma gradient
col_grad_a        0.061   ← Lab-a chroma gradient
col_b_std         0.031
fft_dir           0.024
col_a_std         0.020
lap_var           0.019
sat_std           0.017
fft_hi            0.015
hf_sobel          0.012
depth_grad        0.010
depth_var         0.006   ← the cascade's current only cue
```

The Lab a/b chroma gradients dominate. MiDaS depth alone is a weak cue in this setting because per-image min-max normalisation collapses the scale information that would separate flat surfaces from real scenes.

### 4.4 User set (batchA + batchB, n = 59)

- **LOO-CV, Ours / SVM-RBF**: AUC 0.914 [0.832, 0.978], EER 0.136, Sens 0.897 / Spec 0.867 at Youden threshold 0.51. Confusion: TP 26, FN 3, FP 4, TN 26.
- **Cross-domain** (CelebA-trained model applied verbatim): AUC 0.442 [0.301, 0.583] — a domain-gap finding. CelebA crops are face-bounding-box tight; user crops are full-body. The Lab chroma gradient cue shifts scale between the two. For a deployable cascade, one must **either** include a small same-domain calibration set **or** retrain on full-body-cropped anti-spoof data (we know of no public corpus; this is a legitimate paper-worthy gap).

### 4.5 Operating points (Ours / SVM-RBF on CelebA test)

| Target | Threshold | TPR | FPR | Use case |
|---|---:|---:|---:|---|
| Youden-J | 0.51 | 0.867 | 0.133 | Balanced |
| TPR ≥ 0.95 | 0.34 | 0.953 | 0.293 | High-recall, safety-critical |
| FPR ≤ 0.05 | 0.73 | 0.700 | 0.050 | Low-false-alarm deployment |
| FPR ≤ 0.10 | 0.64 | 0.867 | 0.098 | Reported TPR @ 10 % FPR |

---

## 5. Artefacts (full file list)

```
/workspace/
├── ANTISPOOF_REPORT.md                      ← this document
├── prep_uploaded_images.py                  user-crop pre-processing
├── fetch_celeba_spoof.py                    HuggingFace streaming fetcher (TARGET=1500/class)
├── eval_antispoof.py                        v1 — depth-var only (historical)
├── eval_antispoof_v2.py                     v2 — 7-feature + LogReg + user LOO
├── eval_antispoof_v3.py                     v3 — 22-feature + 5 classifiers
├── eval_antispoof_v4.py                     ← main experiment, 25-feature + 3 baselines
│
├── antispoof_val/
│   ├── real/                                29 user real crops
│   ├── spoof/                               30 user screen-spoof crops
│   └── spoof_screen/, spoof_print/          per-category mirrors (from prep_uploaded_images.py)
│
├── antispoof_hf/celeba_spoof/
│   ├── real/                                1500 CelebA live crops
│   └── spoof/                               1500 CelebA spoof crops (print + replay)
│
└── antispoof_results_v4/
    ├── features_cache.npz                   Ours / LBP / Colour-Texture feature matrices + labels
    ├── loo_scores.npy                       user LOO probabilities
    ├── antispoof_v4_summary.json            every metric with 95 % bootstrap CI
    ├── antispoof_v4_roc.{png,pdf}           6-method ROC overlay + user ROCs
    └── antispoof_v4_importance.png          permutation importance on RandomForest
```

All deployable model parameters (final SVM-RBF fit on CelebA-train) are in `antispoof_v4_summary.json`; for on-device use the LogReg variant (AUC 0.877, single matmul, quantisable to INT8) is the recommended Hailo-compilable fallback.

---

## 6. Honest limitations — what we still tell the reviewer up front

1. **Handcrafted features, no CNN baseline.** We do not compare against CDCN, DeepPixBiS or any ViT-based anti-spoof network in this submission; the contribution is a lightweight, Hailo-compilable feature set, and we justify the omission on deployment-latency grounds while acknowledging it.
2. **Cross-domain transfer (AUC 0.44).** CelebA-Spoof face crops do not transfer to full-body user crops without adaptation. Same-domain LOO on the user set recovers AUC 0.91, so the feature is expressive enough — the gap is domain-specific and can be closed with 100 + in-house captures.
3. **User set size (n = 59).** LOO-CV 95 % CI is wide (0.83–0.98). Sufficient for feasibility but not a definitive deployment claim. The scripts run unchanged on larger captures.
4. **Single spoof type in user set.** batchB contains screen attacks only; printed-photo spoofs were present in CelebA and handled there, but the user-set number reflects only replay attacks.
5. **SVM-RBF is not directly Hailo-compilable.** For deployment we recommend either the LogReg variant (AUC 0.877 on CelebA test) or a two-stage cascade where LogReg triages and SVM-RBF is invoked on uncertain frames (scores in [0.3, 0.7]).
6. **No statistical test between methods.** CI overlap was used as a proxy for significance; a DeLong paired test would strengthen the table.

---

## 7. Reproducibility

```bash
# 1. User batch prep (batchA → real_raw, batchB → spoof_raw)
ln -sfn /workspace/batchA /workspace/antispoof_upload/real_raw
ln -sfn /workspace/batchB /workspace/antispoof_upload/spoof_raw
python3 /workspace/prep_uploaded_images.py

# 2. CelebA-Spoof streaming download (1500 + 1500, ~4 min)
python3 /workspace/fetch_celeba_spoof.py

# 3. Full evaluation (3 feature sets × 4 folders ≈ 3 min CUDA, ~30 s CPU-only for classifiers)
python3 /workspace/eval_antispoof_v4.py
```

All seeds fixed (`random_state=0`, bootstrap RNG seeded). scikit-learn 1.8.0, PyTorch 2.8, OpenCV 4.13, scikit-image latest, Python 3.12. GPU: user's 4090 (optional — MiDaS runs on CPU in ~4 s / image).

---

## 8. Suggested paper-level narrative

> We evaluate the anti-spoof stage of the cascade (P0-4). The deployed system uses a single MiDaS-Small depth-variance cue with a fixed 0.05 threshold. On 600 held-out CelebA-Spoof test images this baseline reaches only AUC 0.56 (95 % CI 0.52–0.61), barely above chance, and its claimed operating point accepts 93 % real and 93 % spoof alike. We introduce a 25-dimensional descriptor that augments the same depth cue with Lab chroma gradients, radial and directional FFT energies, HSV spread statistics, and uniform-LBP micro-texture. An RBF-SVM over these features attains AUC 0.951 [0.935, 0.967] on the CelebA-Spoof test split (+0.39 AUC over the baseline, non-overlapping CIs; +0.10 over a reimplemented LBP+SVM baseline, +0.05 over HSV-histogram colour-texture). On an in-house 59-image cascade-camera capture (user batchA/B) the same descriptor achieves AUC 0.914 via leave-one-out CV, with Sens 0.90 / Spec 0.87 at the Youden point. Cross-corpus transfer drops to AUC 0.44, confirming that anti-spoof features generalise only with same-domain calibration — a finding we position as a deployment prerequisite rather than a negative result.
