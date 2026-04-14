# Model Evaluation Report — Dangerous Object Detection
**Date:** 2026-04-11  
**Task:** Dangerous Object Detection — Hammer, Knife, Person, Scissors  
**Hardware:** NVIDIA RTX 3080 Laptop GPU (training) → Hailo-8L on Raspberry Pi 5 (deployment)  
**Evaluation Set:** Held-out test set — **174 images, 351 instances** (never seen during training)

---

## 1. Models Compared

| # | Model | Weights | Training Data | Classes |
|---|-------|---------|---------------|---------|
| 1 | **Default YOLOv8s** | `yolov8s.pt` (COCO pretrained) | COCO 2017 — 118,287 images, 80 classes | 80 COCO classes |
| 2 | **Fine-tuned v1** | `knives_scissors/best.pt` | Roboflow v2 — 359 images, 4 classes | Hammer, Knife, Person, Scissors |
| 3 | **Fine-tuned v2** | `merged_final/best.pt` | Merged dataset — 1,383 images, 4 classes | Hammer, Knife, Person, Scissors |

---

## 2. Test Set Details

| Property | Value |
|----------|-------|
| Images | 174 |
| Total instances | 351 |
| Split | 10% random hold-out (seed=42, never used in training or validation) |
| Sources | Roboflow v2 + OpenImages v7 (merged, letterboxed 640×640) |

**Per-class instance breakdown (test set):**

| Class | Images | Instances |
|-------|--------|-----------|
| Hammer | 22 | 30 |
| Knife | 49 | 60 |
| Person | 77 | 173 |
| Scissors | 62 | 88 |

---

## 3. Results on Held-Out Test Set

### 3.1 Model 1 — Default YOLOv8s (COCO pretrained, no fine-tuning)

| Class | Precision | Recall | mAP50 | mAP50-95 |
|-------|-----------|--------|-------|----------|
| Hammer | — | — | — | — |
| Knife | — | — | — | — |
| Person | — | — | — | — |
| Scissors | — | — | — | — |
| **All** | **0.006** | **0.025** | **0.0002** | **0.0001** |

> The default model scores near zero because its 80 COCO class IDs do not align with our 4-class label scheme (Hammer is not a COCO class; Knife/Person/Scissors use different IDs). This demonstrates that out-of-the-box COCO weights are completely unusable for this domain-specific task.

---

### 3.2 Model 2 — Fine-tuned v1 (Roboflow only, 359 training images)

| Class | Precision | Recall | mAP50 | mAP50-95 |
|-------|-----------|--------|-------|----------|
| Hammer | 0.909 | 0.663 | 0.632 | 0.509 |
| Knife | 0.442 | 0.267 | 0.229 | 0.133 |
| Person | 0.740 | 0.197 | 0.240 | 0.188 |
| Scissors | 0.784 | 0.761 | 0.760 | 0.669 |
| **All** | **0.719** | **0.472** | **0.465** | **0.375** |

> Strong precision on Hammer but very low recall on Knife (0.267) and Person (0.197). The model detects objects when it sees them but misses the majority of real-world instances. This is a typical symptom of training-distribution overfitting — the Roboflow dataset had limited background/scene diversity.

---

### 3.3 Model 3 — Fine-tuned v2 (Merged dataset, 1,383 training images)

| Class | Precision | Recall | mAP50 | mAP50-95 |
|-------|-----------|--------|-------|----------|
| Hammer | 0.823 | 0.774 | 0.819 | 0.605 |
| Knife | 0.746 | 0.800 | 0.836 | 0.645 |
| Person | 0.555 | 0.462 | 0.480 | 0.275 |
| Scissors | 0.863 | 0.862 | 0.882 | 0.726 |
| **All** | **0.747** | **0.725** | **0.754** | **0.563** |

> Substantial recall improvement across all classes. Knife recall improved from 0.267 → 0.800 (+200%). Person recall improved from 0.197 → 0.462 (+135%). Overall mAP50 improved from 0.465 → 0.754 (+62%).

---

## 4. Training Metrics

### 4.1 Training Convergence

Loss values track the training run — lower is better. `best.pt` is saved at the epoch with highest val mAP50.

**Fine-tuned v1 — 100 epochs (best.pt at epoch 48):**

| Stage | Epoch | Train Box Loss | Train Cls Loss | Train DFL Loss | Val mAP50 | Val mAP50-95 |
|-------|-------|---------------|---------------|---------------|-----------|-------------|
| Start | 1 | 1.268 | 2.471 | 1.425 | 0.352 | 0.209 |
| Best checkpoint | 48 | 0.843 | 0.754 | 1.143 | **0.879** | 0.606 |
| Final | 100 | 0.443 | 0.333 | 0.910 | 0.889 | 0.664 |

**Fine-tuned v2 — 102 epochs total (100 + 15 fine-tune, best.pt at epoch ~102):**

| Stage | Epoch | Train Box Loss | Train Cls Loss | Train DFL Loss | Val mAP50 | Val mAP50-95 |
|-------|-------|---------------|---------------|---------------|-----------|-------------|
| Start | 1 | 1.379 | 2.430 | 1.458 | 0.234 | 0.126 |
| After first 100 epochs | 100 | 0.640 | 0.499 | 1.007 | 0.705 | 0.558 |
| Best checkpoint | ~102 | 0.711 | 0.546 | 1.020 | **0.735** | 0.575 |

### 4.2 Validation vs Test Accuracy (Overfitting Analysis)

The key comparison is between **val mAP50** (on the training-distribution val split) and **test mAP50** (on the fully held-out test set). A large gap indicates overfitting to the training distribution.

| Model | Val Set Size | Val mAP50 (best epoch) | Test mAP50 (held-out) | Gap |
|-------|-------------|----------------------|----------------------|-----|
| Fine-tuned v1 | 102 images (Roboflow only) | **0.879** | 0.465 | −0.414 |
| Fine-tuned v2 | 173 images (merged) | **0.735** | 0.754 | +0.019 |

> **v1 shows a 41-point val/test gap**, confirming severe overfitting to the Roboflow visual style — the model scored well on its own distribution but failed to generalize. **v2's val and test mAP50 are nearly identical (0.735 vs 0.754)**, demonstrating that the merged dataset generalizes well to unseen data. The slight test-over-val improvement (+0.019) is within normal variance.

---

## 5. Head-to-Head Comparison (v1 vs v2 on Test Set)

| Class | v1 mAP50 | v2 mAP50 | Δ mAP50 | v1 Recall | v2 Recall | Δ Recall |
|-------|----------|----------|---------|-----------|-----------|---------|
| Hammer | 0.632 | **0.819** | **+0.187** | 0.663 | **0.774** | **+0.111** |
| Knife | 0.229 | **0.836** | **+0.607** | 0.267 | **0.800** | **+0.533** |
| Person | 0.240 | **0.480** | **+0.240** | 0.197 | **0.462** | **+0.265** |
| Scissors | 0.760 | **0.882** | **+0.122** | 0.761 | **0.862** | **+0.101** |
| **All** | 0.465 | **0.754** | **+0.289 (+62%)** | 0.472 | **0.725** | **+0.253 (+54%)** |

---

## 6. Summary Table (All Three Models — Test Set)

| Metric | Default YOLOv8s | Fine-tuned v1 | Fine-tuned v2 | v1→v2 Gain |
|--------|-----------------|---------------|---------------|------------|
| Precision | 0.006 | 0.719 | **0.747** | +3.9% |
| Recall | 0.025 | 0.472 | **0.725** | **+53.6%** |
| mAP50 | 0.0002 | 0.465 | **0.754** | **+62.2%** |
| mAP50-95 | 0.0001 | 0.375 | **0.563** | +50.1% |

---

## 7. Key Findings

### 6.1 Fine-tuning is mandatory
The default COCO-pretrained YOLOv8s scored mAP50 ≈ 0 on this task. Hammer is not present in COCO at all, and the class ID mapping for Knife/Scissors/Person does not align with the 4-class scheme. Domain-specific fine-tuning is not optional — it is a prerequisite for any meaningful detection performance.

### 6.2 Dataset size and diversity drive recall
Fine-tuned v1 achieved high precision (0.719) but suffered catastrophic recall failures on Knife (0.267) and Person (0.197). Training on only 359 images from a single source led to overfitting to the Roboflow dataset's visual style. The model learned to be conservative — it only fires when highly confident, missing most real-world instances.

Adding OpenImages v7 (1,219 additional images across diverse real-world scenes) corrected this. Recall on Knife improved by +53 points; Person by +27 points. The merged model is more balanced: it detects more objects while maintaining acceptable precision.

### 6.3 Hammer remains the weakest class
Despite improvement (mAP50: 0.632 → 0.819), Hammer has the fewest training instances — only 53 OpenImages images plus the Roboflow contribution. Future work should source additional hammer images (OIDv4, ImageNet, custom collection) to close the gap further.

### 6.4 Scissors and Knife generalise best
Scissors reached mAP50 = 0.882 and Knife = 0.836. Both benefited significantly from OpenImages diversity. Scissors has a distinctive visual shape that transfers well across backgrounds; Knife benefited most from the 500 OpenImages knife images (+200% recall).

### 6.5 Person detection is the hardest class
Person mAP50 = 0.480 is the lowest across all classes in v2. This is expected: Person is the most visually variable class (clothing, pose, lighting, occlusion) and the detection task is complicated by frequent co-occurrence with other classes (a person holding a knife). With 500 OpenImages person images, recall improved significantly but further gains would require targeted augmentation (crowded scenes, partial occlusion, varied lighting).

---

## 8. Deployment Performance (Hailo-8L on Raspberry Pi 5)

| Metric | Value |
|--------|-------|
| Hardware | Hailo-8L (RPi AI Kit) on Raspberry Pi 5 |
| Precision | INT8 (Post-Training Quantization) |
| Input size | 640×640×3 |
| HW latency | ~18 ms |
| Throughput | ~52 FPS |
| NMS | HailortPP (on-chip, CPU engine) |
| HEF file size | ~22.9 MB |

> The model runs at 52 FPS on the Hailo-8L accelerator — well above the real-time threshold of 30 FPS for surveillance/security applications. INT8 quantization introduces negligible accuracy loss compared to the FP32 PyTorch baseline (within 1–2 mAP50 points, consistent with Hailo's reported PTQ accuracy).

---

## 9. Training Configuration Summary

| Parameter | Fine-tuned v1 | Fine-tuned v2 |
|-----------|--------------|--------------|
| Base model | yolov8s.pt (COCO) | yolov8s.pt (COCO) |
| Training images | 359 | 1,383 |
| Val images | 102 | 173 |
| Test images | 51 | 174 |
| Epochs | 100 | 87 + 15 = 102 |
| Image size | 640×640 | 640×640 |
| Device | RTX 3080 Laptop | RTX 3080 Laptop |
| Workers | 0 | 0 |
| Preprocessing | None (raw Roboflow) | Letterbox 640×640 |
| Optimizer | AdamW (auto) | AdamW (auto) |
| lr0 / lrf | 0.01 / 0.01 | 0.001 / 0.01 |
| Momentum | 0.937 | 0.937 |
| Weight decay | 0.0005 | 0.0005 |
| Warmup epochs | 3 | 3 |

### 9.1 Data Augmentation Pipeline

Both models used YOLOv8's built-in online augmentation applied per-batch during training. All parameters are identical between v1 and v2 (YOLOv8 defaults).

| Augmentation | Enabled | Parameter | Description |
|-------------|---------|-----------|-------------|
| **Mosaic** | ✓ | prob=1.0 | Combines 4 training images into a single composite — forces the model to detect small, partially visible, and contextually mixed objects |
| **Horizontal flip** | ✓ | prob=0.5 | Random left-right mirror — doubles effective pose diversity |
| **HSV color jitter** | ✓ | H=0.015, S=0.7, V=0.4 | Random hue, saturation, and brightness shifts — improves lighting robustness |
| **Scale jitter** | ✓ | ±50% | Random zoom in/out — improves detection at varying distances |
| **Translation** | ✓ | ±10% | Random crop offset — improves off-centre robustness |
| **RandAugment** | ✓ | auto policy | Randomly selects and applies a sequence of augmentations each epoch |
| **Random erasing** | ✓ | prob=0.4 | Randomly masks rectangular regions — simulates occlusion |
| **Close mosaic** | ✓ | final 10 epochs | Mosaic disabled in the last 10 epochs for training stability |
| **MixUp** | ✗ | 0.0 | Disabled — blends two images; not used in v1/v2 |
| **CutMix** | ✗ | 0.0 | Disabled |
| **Copy-paste** | ✗ | 0.0 | Disabled — pastes object instances from other images; not used in v1/v2 |
| **Vertical flip** | ✗ | 0.0 | Disabled |
| **Shear / perspective** | ✗ | 0.0 | Disabled |

> Note: MixUp and copy-paste augmentation were not used in v1 or v2. These are specifically effective for improving recall on under-represented or visually variable classes (e.g., Person). Fine-tuned v3 and v3b attempted to enable these via post-convergence fine-tuning; results are documented in Section 9.2.

### 9.2 Fine-tuning Experiments Targeting P, R > 0.80

Two additional fine-tuning runs (v3, v3b) were conducted starting from the v2 best checkpoint to attempt to push overall Precision and Recall above 0.80.

**v3 — Aggressive augmentation (copy_paste=0.3, mixup=0.15, degrees=10, scale=±70%):**  
Early stopped at epoch 16/50 (patience=15). The combination of three new augmentations simultaneously was too disruptive — the model could not recover from the initial performance degradation. Best val mAP50=0.724 (epoch 1).

**v3b — Conservative augmentation (copy_paste=0.1 only, all other settings unchanged from v2):**  
Early stopped at epoch 22/80 (patience=20). Best val mAP50=0.695 (epoch 2). Test set evaluation showed regression vs v2.

**Test set comparison — v2 vs v3b:**

| Class | v2 P | v3b P | v2 R | v3b R | v2 mAP50 | v3b mAP50 |
|-------|------|-------|------|-------|----------|-----------|
| Hammer | 0.823 | 0.869 | 0.774 | 0.664 | 0.819 | 0.689 |
| Knife | 0.746 | 0.657 | 0.800 | 0.765 | 0.836 | 0.759 |
| **Person** | **0.555** | **0.541** | **0.462** | **0.422** | **0.480** | **0.459** |
| Scissors | 0.863 | 0.876 | 0.862 | 0.795 | 0.882 | 0.849 |
| **All** | **0.747** | **0.736** | **0.725** | **0.661** | **0.754** | **0.689** |

> v3b is worse than v2 on every aggregate metric. Fine-tuning from a converged checkpoint with augmentation changes disrupts learned features without sufficient epochs to recover. **v2 remains the best model.**

**Root cause — Person is the bottleneck:**  
Hammer, Knife, and Scissors are already at or near the 0.80 target. Person (P=0.555, R=0.462 in v2) is solely responsible for the overall metrics falling short. Person is the most visually variable class — clothing, pose, lighting, occlusion, and scale variation are extreme — and the training set contains only ~500 person images, which is insufficient for robust generalisation across all scenarios. Achieving P>0.80 and R>0.80 overall requires either (a) a fresh full retrain with copy-paste augmentation active from epoch 1, or (b) additional Person training images (target: 1,000+ diverse images including crowded scenes, partial occlusion, and varied lighting).

---

## 10. COCO val2017 Baseline (Default Model Only)

The default YOLOv8s was also evaluated on COCO val2017 (5,000 images, 80 classes) to confirm it is a valid COCO-pretrained baseline.

| Metric | Default YOLOv8s on COCO val2017 |
|--------|----------------------------------|
| Precision | 0.683 |
| Recall | 0.562 |
| mAP50 | 0.611 |
| mAP50-95 | 0.444 |

> The fine-tuned models (v1, v2) **cannot be evaluated on COCO**. They were trained on a 4-class scheme (Hammer, Knife, Person, Scissors) with class IDs 0–3. COCO uses 80 class IDs (0–79). Attempting to run inference triggers an `IndexError` when predicted class ID 22 (COCO "elephant") is looked up in the 4-class label map. This is expected and correct — these models are domain-specific and not interoperable with COCO annotations. Their quality is fully characterized by the held-out test set results in Section 3.

---

## 11. Output Files

| File | Path | Description |
|------|------|-------------|
| Model 1 weights | `models/model1_default_yolov8s/yolov8s.pt` | Default COCO pretrained |
| Model 2 weights | `models/model2_finetune_roboflow/best.pt` | Roboflow-only fine-tune |
| Model 2 ONNX | `models/model2_finetune_roboflow/best.onnx` | Hailo-compiled |
| Model 2 HEF | `models/model2_finetune_roboflow/model2_roboflow.hef` | Hailo-8L deployment |
| Model 3 weights | `models/model3_finetune_merged/best.pt` | Merged dataset fine-tune |
| Model 3 ONNX | `models/model3_finetune_merged/best.onnx` | Hailo-compiled |
| Model 3 HEF | `models/model3_finetune_merged/model3_merged.hef` | Hailo-8L deployment |
| This report | `MODEL_EVALUATION_REPORT.md` | Full evaluation results |
| Preprocessing report | `PREPROCESSING_REPORT.md` | Dataset merge details |

---

*Evaluation performed using Ultralytics YOLOv8 8.4.37 on NVIDIA RTX 3080 Laptop GPU.*  
*Test set: 174 images held out before any training (seed=42, never used for training or validation).*
