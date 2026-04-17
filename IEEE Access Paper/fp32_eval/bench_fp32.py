import sys
sys.path.insert(0, '/workspace/train_venv/lib/python3.11/site-packages')
import torch, time, json
from ultralytics import YOLO

m = YOLO('/workspace/project/runs/train_v5/weights/best.pt')
net = m.model.to('cuda').eval().float()

def bench(bs):
    x = torch.randn(bs, 3, 640, 640, device='cuda', dtype=torch.float32)
    with torch.no_grad():
        for _ in range(50):
            net(x)
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        for _ in range(500):
            net(x)
        torch.cuda.synchronize()
        t1 = time.perf_counter()
    total_imgs = 500 * bs
    ms_per_img = (t1 - t0) * 1000 / total_imgs
    fps = total_imgs / (t1 - t0)
    return ms_per_img, fps

r = {}
for bs in (1, 32):
    ms, fps = bench(bs)
    r[f'b{bs}'] = {'ms_per_img': ms, 'fps': fps}
    print(f'batch={bs}: {ms:.3f} ms/img, {fps:.1f} FPS')

with open('/workspace/bench_fp32_results.json', 'w') as f:
    json.dump(r, f, indent=2)
