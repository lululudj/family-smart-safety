# -*- coding: utf-8 -*-
"""自举第3步：种子模型切片推理全量重检测。
- 负样本帧：conf>=0.25 的检出写入标签
- 正样本帧：不动（种子已验证）
- 输出全部正标签核验九宫格供 是/否 复核
"""
import os, json
import numpy as np
import cv2
import torch
from ultralytics import YOLO

ROOT = r"e:\全家智能安全调度系统"
DS = os.path.join(ROOT, "datasets", "tello_kinect")
OUT = os.path.join(DS, "relabel_20261017")
SEED_WT = os.path.join(DS, "exports", "tello_n_seed", "weights", "best.pt")

def imread_cn(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)

def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0

model = YOLO(SEED_WT)
device = "cuda:0" if torch.cuda.is_available() else "cpu"
print("bootstrap device:", device, os.path.basename(SEED_WT))

def detect(img, conf, imgsz):
    out = []
    with torch.no_grad():
        r = model.predict(img, conf=conf, verbose=False, imgsz=imgsz, device=device)[0]
        for b in r.boxes:
            out.append((float(b.conf), tuple(float(v) for v in b.xyxy[0])))
    return out

def tiled(img):
    H, W = img.shape[:2]
    cands = detect(img, 0.10, 960)
    hw, hh = W // 2, H // 2
    for ti in range(4):
        x0, y0 = (ti % 2) * hw, (ti // 2) * hh
        big = cv2.resize(img[y0:y0 + hh, x0:x0 + hw], (960, 540), interpolation=cv2.INTER_CUBIC)
        for conf, bb in detect(big, 0.12, 960):
            cands.append((conf, (bb[0] / 2 + x0, bb[1] / 2 + y0, bb[2] / 2 + x0, bb[3] / 2 + y0)))
    cands.sort(key=lambda c: -c[0])
    kept = []
    for c in cands:
        if all(iou(c[1], k[1]) < 0.45 for k in kept):
            kept.append(c)
    return [c for c in kept if 14 <= (c[1][2] - c[1][0]) <= 400 and 12 <= (c[1][3] - c[1][1]) <= 400]

stats = {"neg_new": 0, "neg_hit": 0, "pos_frames": 0}
hits = {}
for split in ("train", "val"):
    idir = os.path.join(DS, "images", split)
    ldir = os.path.join(DS, "labels", split)
    for fn in sorted(os.listdir(idir)):
        if not fn.endswith(".jpg"):
            continue
        lp = os.path.join(ldir, fn[:-4] + ".txt")
        has = os.path.isfile(lp) and os.path.getsize(lp) > 0
        if has:
            stats["pos_frames"] += 1
            continue
        img = imread_cn(os.path.join(idir, fn))
        H, W = img.shape[:2]
        kept = tiled(img)
        if kept and kept[0][0] >= 0.25:
            conf, bb = kept[0]
            with open(lp, "w", encoding="utf-8") as f:
                f.write(f"0 {(bb[0]+bb[2])/2/W:.6f} {(bb[1]+bb[3])/2/H:.6f} "
                        f"{(bb[2]-bb[0])/W:.6f} {(bb[3]-bb[1])/H:.6f}\n")
            stats["neg_new"] += 1
            hits[fn[:-4]] = round(conf, 2)
        else:
            stats["neg_hit"] += 1

with open(os.path.join(OUT, "bootstrap_hits.json"), "w", encoding="utf-8") as f:
    json.dump(hits, f, ensure_ascii=False, indent=1)
print(json.dumps(stats, ensure_ascii=False))

# 核验图（全部正标签）
cells = []
for split in ("train", "val"):
    idir = os.path.join(DS, "images", split)
    for fn in sorted(os.listdir(idir)):
        if not fn.endswith(".jpg"):
            continue
        lp = os.path.join(DS, "labels", split, fn[:-4] + ".txt")
        if not (os.path.isfile(lp) and os.path.getsize(lp) > 0):
            continue
        img = imread_cn(os.path.join(idir, fn))
        h, w = img.shape[:2]
        for line in open(lp, encoding="utf-8"):
            p = line.split()
            if len(p) == 5:
                _, xc, yc, bw, bh = map(float, p)
                cv2.rectangle(img, (int((xc - bw / 2) * w), int((yc - bh / 2) * h)),
                              (int((xc + bw / 2) * w), int((yc + bh / 2) * h)), (0, 0, 255), 2)
        img = cv2.resize(img, (480, 270))
        cv2.putText(img, fn[-13:], (4, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cells.append(img)
n = 0
for k in range(0, len(cells), 9):
    b = cells[k:k + 9]
    while len(b) < 9:
        b.append(np.zeros((270, 480, 3), np.uint8))
    rows = [np.hstack(b[i:i + 3]) for i in range(0, 9, 3)]
    n += 1
    cv2.imencode(".jpg", np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 86])[1].tofile(
        os.path.join(OUT, f"bs_{n:02d}.jpg"))
print("bootstrap verification grids:", n)
