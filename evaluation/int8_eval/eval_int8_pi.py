"""On-device INT8 mAP evaluation for best_v5.hef on Hailo-8L.

Runs the deployed HEF (with on-chip yolov8 NMS) on the dataset_v5 test split
(622 images, 1656 instances) and computes mAP@0.5 and mAP@0.5:0.95 using
COCO-style 101-point interpolation matched to ultralytics' DetMetrics, so the
result is directly comparable to the FP32 number (mAP@0.5 = 0.847).

Why on-Pi: the HEF on the Hailo-8L is bit-accurate to deployment. The SDK
emulator path on the RunPod produced near-zero mAP because the HailortPP NMS
output layout did not match the bbox-decoder assumption in eval_int8_test.py.
HailoRT InferVStreams returns the documented NMS_BY_CLASS format, decoded in
basic_pipelines/garuda_cascade.py:299-320.
"""
import json
import time
from pathlib import Path

import cv2
import numpy as np

from hailo_platform import (
    HEF, VDevice, ConfigureParams, HailoStreamInterface,
    InferVStreams, InputVStreamParams, OutputVStreamParams, FormatType,
)

HEF_PATH   = "/home/manikanta/Projects/Garuda_26/resources/best_v5.hef"
TEST_IMG   = Path("/home/manikanta/Projects/Garuda_26/evaluation/int8_eval/test/images")
TEST_LBL   = Path("/home/manikanta/Projects/Garuda_26/evaluation/int8_eval/test/labels")
OUT_JSON   = "/home/manikanta/Projects/Garuda_26/evaluation/int8_eval/eval_int8_pi_results.json"
NAMES = {0: "Hammer", 1: "Knife", 2: "Person", 3: "scissors"}
NC    = 4
IMG   = 640
CONF_TH = 0.001          # academic eval (matches FP32 val: conf=0.001)
IOU_THRESHOLDS = np.linspace(0.5, 0.95, 10)


# ----------------------------- preprocessing ---------------------------------

def letterbox(img_bgr, size=IMG):
    """YOLO letterbox to size×size, pad with 114, return img + (r, padx, pady)."""
    h, w = img_bgr.shape[:2]
    r = min(size / h, size / w)
    nh, nw = int(round(h * r)), int(round(w * r))
    if (nh, nw) != (h, w):
        img_bgr = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    top  = (size - nh) // 2
    left = (size - nw) // 2
    canvas[top:top + nh, left:left + nw] = img_bgr
    return canvas, r, left, top


def load_gt(label_path: Path, w: int, h: int):
    """YOLO txt -> Nx5 ndarray [cls, x1, y1, x2, y2] in original-image px."""
    if not label_path.exists():
        return np.zeros((0, 5), dtype=np.float32)
    rows = []
    for line in label_path.read_text().splitlines():
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        c, xc, yc, bw, bh = map(float, parts[:5])
        rows.append([
            int(c),
            (xc - bw / 2) * w,
            (yc - bh / 2) * h,
            (xc + bw / 2) * w,
            (yc + bh / 2) * h,
        ])
    return np.asarray(rows, dtype=np.float32) if rows else np.zeros((0, 5), dtype=np.float32)


# ----------------------------- IoU + mAP -------------------------------------

def box_iou(a, b):
    """Pairwise IoU. a:[N,4], b:[M,4] -> [N,M]. Boxes are x1,y1,x2,y2."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    a_x1, a_y1, a_x2, a_y2 = a[:, 0:1], a[:, 1:2], a[:, 2:3], a[:, 3:4]
    b_x1, b_y1, b_x2, b_y2 = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    inter_x1 = np.maximum(a_x1, b_x1); inter_y1 = np.maximum(a_y1, b_y1)
    inter_x2 = np.minimum(a_x2, b_x2); inter_y2 = np.minimum(a_y2, b_y2)
    inter = np.clip(inter_x2 - inter_x1, 0, None) * np.clip(inter_y2 - inter_y1, 0, None)
    area_a = (a_x2 - a_x1) * (a_y2 - a_y1)
    area_b = (b_x2 - b_x1) * (b_y2 - b_y1)
    union = area_a + area_b - inter + 1e-12
    return inter / union


def match_predictions(pred_xyxy, pred_cls, gt_xyxy, gt_cls):
    """Greedy match preds (already conf-sorted desc) to GT for each IoU thr.
    Returns correct[npred, niou] bool: pred i is TP at IoU thr j.
    """
    npred = len(pred_xyxy); niou = len(IOU_THRESHOLDS)
    correct = np.zeros((npred, niou), dtype=bool)
    if npred == 0 or len(gt_xyxy) == 0:
        return correct
    iou = box_iou(gt_xyxy, pred_xyxy)                    # [ngt, npred]
    cls_match = (gt_cls[:, None] == pred_cls[None, :])   # [ngt, npred]
    for ti, thr in enumerate(IOU_THRESHOLDS):
        ok = (iou >= thr) & cls_match
        if not ok.any():
            continue
        # greedy: each pred can match at most one GT and vice versa, by IoU order
        pairs = np.argwhere(ok)
        scores = iou[pairs[:, 0], pairs[:, 1]]
        order = scores.argsort()[::-1]
        pairs = pairs[order]
        used_gt = set(); used_pred = set()
        for gi, pi in pairs:
            if gi in used_gt or pi in used_pred:
                continue
            used_gt.add(gi); used_pred.add(pi)
            correct[pi, ti] = True
    return correct


def ap_101pt(recall, precision):
    """COCO-style 101-point interpolated AP."""
    mrec = np.concatenate(([0.0], recall, [1.0]))
    mpre = np.concatenate(([1.0], precision, [0.0]))
    # monotonic-decreasing precision envelope
    for i in range(len(mpre) - 1, 0, -1):
        mpre[i - 1] = max(mpre[i - 1], mpre[i])
    x = np.linspace(0, 1, 101)
    ap = np.trapz(np.interp(x, mrec, mpre), x)
    return ap


def compute_map(stats):
    """stats = list of (correct[N,niou], conf[N], pred_cls[N], target_cls[M])."""
    if not stats:
        return None
    correct = np.concatenate([s[0] for s in stats], 0) if any(len(s[0]) for s in stats) else np.zeros((0, len(IOU_THRESHOLDS)), dtype=bool)
    conf    = np.concatenate([s[1] for s in stats], 0) if any(len(s[1]) for s in stats) else np.zeros((0,), dtype=np.float32)
    pcls    = np.concatenate([s[2] for s in stats], 0) if any(len(s[2]) for s in stats) else np.zeros((0,), dtype=np.int32)
    tcls    = np.concatenate([s[3] for s in stats], 0) if any(len(s[3]) for s in stats) else np.zeros((0,), dtype=np.int32)

    # sort all predictions globally by descending conf
    order = conf.argsort()[::-1]
    correct = correct[order]; conf = conf[order]; pcls = pcls[order]

    classes = np.unique(tcls).astype(int)
    niou = len(IOU_THRESHOLDS)
    ap = np.zeros((len(classes), niou), dtype=np.float64)
    p_at_max_f1 = np.zeros(len(classes), dtype=np.float64)
    r_at_max_f1 = np.zeros(len(classes), dtype=np.float64)

    for ci, c in enumerate(classes):
        i = pcls == c
        n_gt = int((tcls == c).sum())
        n_p  = int(i.sum())
        if n_p == 0 or n_gt == 0:
            continue
        c_correct = correct[i]      # [n_p, niou]
        c_conf    = conf[i]         # already desc-sorted globally
        # cumulative TP/FP at each IoU thr
        tp_cum = np.cumsum(c_correct, axis=0).astype(np.float64)
        fp_cum = np.cumsum(1 - c_correct, axis=0).astype(np.float64)
        recall    = tp_cum / (n_gt + 1e-12)
        precision = tp_cum / (tp_cum + fp_cum + 1e-12)
        for ti in range(niou):
            ap[ci, ti] = ap_101pt(recall[:, ti], precision[:, ti])
        # P/R reported at IoU=0.5 max-F1 conf for compatibility with FP32 report
        f1 = 2 * precision[:, 0] * recall[:, 0] / (precision[:, 0] + recall[:, 0] + 1e-12)
        k  = int(np.argmax(f1))
        p_at_max_f1[ci] = precision[k, 0]
        r_at_max_f1[ci] = recall[k, 0]

    ap50  = ap[:, 0]
    ap5095 = ap.mean(axis=1)
    return {
        "map50":  float(ap50.mean()) if len(ap50) else 0.0,
        "map":    float(ap5095.mean()) if len(ap5095) else 0.0,
        "mp":     float(p_at_max_f1.mean()) if len(p_at_max_f1) else 0.0,
        "mr":     float(r_at_max_f1.mean()) if len(r_at_max_f1) else 0.0,
        "per_class": {
            int(c): {
                "ap50":   float(ap50[ci]),
                "ap5095": float(ap5095[ci]),
                "p":      float(p_at_max_f1[ci]),
                "r":      float(r_at_max_f1[ci]),
                "name":   NAMES.get(int(c), str(int(c))),
            } for ci, c in enumerate(classes)
        },
    }


# ----------------------------- inference -------------------------------------

def main():
    img_paths = sorted(list(TEST_IMG.glob("*.jpg")) + list(TEST_IMG.glob("*.png")))
    print(f"Test images: {len(img_paths)}")
    if not img_paths:
        raise SystemExit("No test images found")

    hef = HEF(HEF_PATH)
    with VDevice() as vdev:
        cfg_params = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
        ng = vdev.configure(hef, cfg_params)[0]
        ivp = InputVStreamParams.make(ng, format_type=FormatType.UINT8)
        ovp = OutputVStreamParams.make(ng, format_type=FormatType.FLOAT32)
        in_name = list(ivp.keys())[0]
        print(f"Input: {in_name}; outputs: {list(ovp.keys())}")

        stats = []
        t0 = time.perf_counter()
        with ng.activate():
            with InferVStreams(ng, ivp, ovp) as p_inf:
                for n, img_path in enumerate(img_paths, 1):
                    img_bgr = cv2.imread(str(img_path))
                    if img_bgr is None:
                        print(f"  skip unreadable {img_path.name}")
                        continue
                    h0, w0 = img_bgr.shape[:2]
                    lb, r, padx, pady = letterbox(img_bgr, IMG)
                    rgb = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB)
                    inp = rgb[np.newaxis].astype(np.uint8)

                    raw = p_inf.infer({in_name: inp})

                    # Decode HailoRT NMS_BY_CLASS output (matches garuda_cascade.py:303-320)
                    pred_rows = []  # (x1, y1, x2, y2, conf, cls) in original image px
                    for tensor in raw.values():
                        if isinstance(tensor, list):
                            class_list = tensor[0] if (tensor and isinstance(tensor[0], list)) else tensor
                            for cls_id, cls_dets in enumerate(class_list):
                                arr = np.asarray(cls_dets, dtype=np.float32)
                                if arr.size == 0:
                                    continue
                                arr = arr.reshape(-1, 5)
                                # cols: y1n, x1n, y2n, x2n, conf normalized to letterboxed 640x640
                                y1n, x1n, y2n, x2n, conf = (arr[:, k] for k in range(5))
                                m = conf >= CONF_TH
                                if not m.any():
                                    continue
                                x1n, y1n, x2n, y2n, conf = x1n[m], y1n[m], x2n[m], y2n[m], conf[m]
                                # px in letterboxed canvas
                                x1p = x1n * IMG; x2p = x2n * IMG
                                y1p = y1n * IMG; y2p = y2n * IMG
                                # remove pad, divide by ratio -> original image coords
                                x1o = (x1p - padx) / r; x2o = (x2p - padx) / r
                                y1o = (y1p - pady) / r; y2o = (y2p - pady) / r
                                for j in range(len(conf)):
                                    pred_rows.append((x1o[j], y1o[j], x2o[j], y2o[j], conf[j], cls_id))
                        else:
                            arr = np.asarray(tensor).reshape(-1, 6)
                            for row in arr:
                                y1n, x1n, y2n, x2n, conf, cls = row
                                if conf < CONF_TH:
                                    continue
                                pred_rows.append((
                                    (x1n * IMG - padx) / r,
                                    (y1n * IMG - pady) / r,
                                    (x2n * IMG - padx) / r,
                                    (y2n * IMG - pady) / r,
                                    float(conf), int(cls),
                                ))

                    if pred_rows:
                        pred_arr = np.asarray(pred_rows, dtype=np.float32)
                        # sort by conf desc (per-image; global sort happens in compute_map)
                        order = pred_arr[:, 4].argsort()[::-1]
                        pred_arr = pred_arr[order]
                        pred_xyxy = pred_arr[:, :4]
                        pred_conf = pred_arr[:, 4]
                        pred_cls  = pred_arr[:, 5].astype(np.int32)
                    else:
                        pred_xyxy = np.zeros((0, 4), dtype=np.float32)
                        pred_conf = np.zeros((0,), dtype=np.float32)
                        pred_cls  = np.zeros((0,), dtype=np.int32)

                    gt = load_gt(TEST_LBL / (img_path.stem + ".txt"), w0, h0)
                    gt_cls  = gt[:, 0].astype(np.int32) if len(gt) else np.zeros((0,), dtype=np.int32)
                    gt_xyxy = gt[:, 1:5] if len(gt) else np.zeros((0, 4), dtype=np.float32)

                    correct = match_predictions(pred_xyxy, pred_cls, gt_xyxy, gt_cls)
                    stats.append((correct, pred_conf, pred_cls, gt_cls))

                    if n % 50 == 0:
                        elapsed = time.perf_counter() - t0
                        print(f"  {n}/{len(img_paths)}  {elapsed:.1f}s  ({n/elapsed:.1f} img/s)", flush=True)

        elapsed = time.perf_counter() - t0
        print(f"Done: {len(stats)} images in {elapsed:.1f}s ({len(stats)/elapsed:.1f} img/s)")

    print("Computing mAP ...")
    res = compute_map(stats)

    print("\n=== INT8 (Hailo-8L on-chip, best_v5.hef) on TEST split ===")
    print(f"  mAP@0.5      : {res['map50']:.4f}")
    print(f"  mAP@0.5:0.95 : {res['map']:.4f}")
    print(f"  Precision    : {res['mp']:.4f}")
    print(f"  Recall       : {res['mr']:.4f}")
    print(f"  Per-class:")
    for c, d in sorted(res["per_class"].items()):
        print(f"    {d['name']:8s}  P={d['p']:.4f}  R={d['r']:.4f}  mAP@0.5={d['ap50']:.4f}  mAP@0.5:0.95={d['ap5095']:.4f}")

    res["meta"] = {
        "hef":  HEF_PATH,
        "n_images": len(stats),
        "conf_threshold": CONF_TH,
        "iou_thresholds": IOU_THRESHOLDS.tolist(),
        "preprocessing": "letterbox to 640x640, pad=114",
        "elapsed_seconds": round(elapsed, 2),
    }
    Path(OUT_JSON).write_text(json.dumps(res, indent=2))
    print(f"\nWrote {OUT_JSON}")


if __name__ == "__main__":
    main()
