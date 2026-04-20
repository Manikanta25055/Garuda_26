#!/usr/bin/env bash
# v6 pipeline: download extra Person → build v6 dataset → train v6
set -e
cd /workspace/project

echo "======================================================"
echo " Step 1 — Download 2000 more Person images (OI val)"
echo "======================================================"
python3 download_person_extra.py 2>&1 | tee download_person_extra_log.txt

echo ""
echo "======================================================"
echo " Step 2 — Build dataset v6"
echo "======================================================"
python3 build_dataset_v6.py 2>&1 | tee build_v6_log.txt

echo ""
echo "======================================================"
echo " Step 3 — Train v6 (yolov8s, copy_paste=0.5, 200 ep)"
echo "======================================================"
python3 train_v6.py 2>&1 | tee train_v6_log.txt

echo ""
echo "======================================================"
echo " Pipeline v6 complete!"
echo "   Best: /workspace/project/runs/train_v6/weights/best.pt"
echo "   Copy: /workspace/project/models/v6_best.pt"
echo "======================================================"
cp /workspace/project/runs/train_v6/weights/best.pt /workspace/project/models/v6_best.pt
