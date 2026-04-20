import sys
sys.path.insert(0, '/workspace/train_venv/lib/python3.11/site-packages')
from ultralytics import YOLO
import torch, time, json

model = YOLO('/workspace/project/runs/train_v5/weights/best.pt')
r = model.val(
    data='/workspace/datasets/dataset_v5/data.yaml',
    split='test', imgsz=640, batch=16,
    conf=0.001, iou=0.7, device=0,
    save_json=False, plots=False, verbose=True,
)
out = {
    'map50': float(r.box.map50),
    'map': float(r.box.map),
    'mp': float(r.box.mp),
    'mr': float(r.box.mr),
    'names': r.names,
    'maps': [float(x) for x in r.box.maps],
    'ap50': [float(x) for x in r.box.ap50],
    'ap_class_index': [int(x) for x in r.box.ap_class_index],
    'nc': int(r.box.nc),
    'p': [float(x) for x in r.box.p],
    'r': [float(x) for x in r.box.r],
}
with open('/workspace/eval_fp32_test_results.json', 'w') as f:
    json.dump(out, f, indent=2)
print('SAVED')
print(json.dumps(out, indent=2))
