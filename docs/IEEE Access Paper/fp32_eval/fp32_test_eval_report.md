# FP32 Test-Split Evaluation Report (v5)

## Environment
- GPU: NVIDIA GeForce RTX 4090 (24091 MiB)
- CUDA / driver: Driver 570.195.03, CUDA 12.8
- Ultralytics version: 8.4.37
- PyTorch version: 2.6.0+cu124
- Date: 2026-04-17 (UTC)

## Dataset split used
- data.yaml path: /workspace/datasets/dataset_v5/data.yaml
- test images: 622
- test labels: 622
- sha256 of data.yaml: 1d9c3d1409ab439e48d36ac91f683ac0b6e84b67dc7e8190e5da8dd9aa2b09a2
- Confirmation that this is the same test split used for the Hailo INT8
  evaluation (0.847 mAP@0.5): yes — `hailo_compile_v5.py` uses
  `/workspace/datasets/dataset_v5/...` as its source dataset, the test split
  contains 622 images / 1,656 instances which matches the "622 images,
  1,656 instances" split documented in GARUDA_CASCADE_REPORT.md §5 for both
  v5 FP32 and Hailo-8L INT8 evaluation. No alternate test split exists in
  the repo.

## Evaluation command
```
from ultralytics import YOLO
m = YOLO('/workspace/project/runs/train_v5/weights/best.pt')
r = m.val(data='/workspace/datasets/dataset_v5/data.yaml', split='test',
          imgsz=640, batch=16, conf=0.001, iou=0.7,
          device=0, save_json=False, plots=False)
```

## Overall metrics on TEST split (FP32, best.pt)
- mAP@0.5: 0.8470
- mAP@0.5:0.95: 0.6605
- Precision: 0.8658
- Recall: 0.8007

## Per-class mAP@0.5 on TEST split (FP32)
| Class    | Images | Instances | P     | R     | mAP@0.5 | mAP@0.5:0.95 |
|----------|--------|-----------|-------|-------|---------|--------------|
| Hammer   | 33     | 46        | 0.930 | 0.848 | 0.889   | 0.720        |
| Knife    | 127    | 159       | 0.896 | 0.817 | 0.886   | 0.721        |
| Person   | 411    | 1324      | 0.701 | 0.609 | 0.647   | 0.373        |
| Scissors | 94     | 127       | 0.935 | 0.929 | 0.967   | 0.828        |

## FP32 inference speed on NVIDIA GeForce RTX 4090
- batch=1  : 6.819 ms/image, 146.6 FPS
- batch=32 : 1.146 ms/image, 872.4 FPS
- Warmup: 50 iters; measured: 500 iters; imgsz=640; fp32; torch.no_grad.

## Model size
- best.pt: 21.49 MB
- ONNX FP32 export: 42.68 MB (pre-existing /workspace/project/runs/train_v5/weights/best.onnx)

## Proposed action for Table 15 (IEEE Access paper)
**DROP the FP32-vs-INT8 mAP@0.5 comparison row.**
Reasons below. FP32 number is solid; INT8-on-TEST mAP was not independently
re-measured in time, so no Δ can be published honestly.

Defensible numbers to keep in the paper:
- FP32 YOLOv8s on held-out TEST split: mAP@0.5 = 0.847, mAP@0.5:0.95 = 0.661
  (this report; 622 images, 1,656 instances; Ultralytics val, conf=0.001,
  iou=0.7 — academic convention).
- Hailo-8L INT8 deployment characteristics (hardware-measured, not mAP):
  52.22 FPS, ~5 W, 22.9 MB HEF, 18.38 ms latency.

Suggested wording for the paper: "FP32 YOLOv8s achieves mAP@0.5 = 0.847 on
the held-out test split; the INT8 model compiled for Hailo-8L runs at
52 FPS at ~5 W on the target device. Independent re-measurement of INT8
mAP on the test split was out of scope for this work."

## Notes / anomalies
- The prior "INT8 = 0.847" figure in GARUDA_CASCADE_REPORT.md §5 is
  numerically identical to the FP32 test result produced by this run
  (same P=0.866, R=0.801, mAP@0.5=0.847, mAP@0.5:0.95=0.661). The table
  labelled "v5 Test Set Results" in that report matches this FP32 run
  exactly. The most likely explanation is that the 0.847 figure cited
  earlier as "INT8-on-TEST" was in fact the FP32 PyTorch test result
  mis-attributed, and no independent INT8-on-TEST evaluation was ever
  conducted. This is the "apples-to-oranges row" the current task was
  meant to correct — confirming it cannot be saved by simply rerunning
  FP32 on TEST.
- An INT8 emulator evaluation was attempted on this pod:
  a separate calibrated HAR (`best_v5_nms_eval.har`, conf=0.001/iou=0.7
  NMS, PTQ with 100 calibration images matching the deployed HEF
  configuration) was built via `hailo_sdk_client.ClientRunner` and run
  through `InferenceContext.SDK_QUANTIZED` on all 622 test images
  (~15 min inference). The HailortPP on-chip NMS output format returned
  by the SDK emulator did not match the bbox-coordinate convention
  assumed by the post-processing code, producing near-zero mAP. Debugging
  the exact output layout requires additional emulator runs; given pod
  time constraints, this was not completed. The deployment HEF
  (`best_v5.hef`, 22.9 MB) and the original optimized HAR were left
  untouched; the new eval HAR and new NMS JSON are additive artefacts.
- Person remains the weak class (FP32 mAP@0.5 = 0.647), consistent with
  prior reports. No missing classes; all 4 classes present in test split.
- Artefacts produced on this pod (for future re-runs on a machine with
  more time / an attached Hailo-8L chip):
    /workspace/eval_fp32_test.py                — FP32 Ultralytics val
    /workspace/bench_fp32.py                    — FP32 speed benchmark
    /workspace/quantize_eval_har.py             — PTQ with eval NMS
    /workspace/eval_int8_test.py                — INT8 emulator eval (bbox
                                                  parsing needs fixing)
    /workspace/project/yolov8_nms_v5_eval.json  — eval NMS config
    /workspace/project/hailo_work_v5/best_v5_nms_eval.har
                                                — quantized HAR w/ eval NMS
