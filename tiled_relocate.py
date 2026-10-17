# -*- coding: utf-8 -*-
"""切片推理重定位：
- 全帧 + 2x2 切片(放大2x) 推理，合并去重
- 正样本：检测结果距现框中心<110px 则替换现框；否则标记 manual（保留现框待处理）
- 负样本：高置信检出(conf>=0.25)则写入新框
"""
import os, json
import numpy as np
import cv2
import torch
from ultralytics import YOLO

ROOT = r"e:\全家智能安全调度系统"
DS = os.path.join(ROOT, "datasets", "tello_kinect")
OUT = os.path.join(DS, "relabel_20261017")

def imread_cn(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)

def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0

ded = YOLO(os.path.join(DS, "weights", "tello_yolov8n.pt"))
world = YOLO(os.path.join(ROOT, "yolov8s-worldv2.pt"))
world.set_classes(["toy drone", "drone"])
device = "cuda:0" if torch.cuda.is_available() else "cpu"
print("device:", device)

def detect(img, conf_ded, conf_wld, imgsz):
    out = []
    with torch.no_grad():
        r = ded.predict(img, conf=conf_ded, verbose=False, imgsz=imgsz, device=device)[0]
        for b in r.boxes:
            out.append((float(b.conf), tuple(float(v) for v in b.xyxy[0])))
        r = world.predict(img, conf=conf_wld, verbose=False, imgsz=imgsz, device=device)[0]
        for b in r.boxes:
            out.append((float(b.conf) * 0.9, tuple(float(v) for v in b.xyxy[0])))
    return out

def tiled_detect(img):
    """全帧 + 2x2 切片。返回合并后的候选 [(conf, xyxy)]"""
    H, W = img.shape[:2]
    cands = []
    cands += detect(img, 0.12, 0.08, 960)
    half_w, half_h = W // 2, H // 2
    for ti in range(4):
        x0, y0 = (ti % 2) * half_w, (ti // 2) * half_h
        tile = img[y0:y0 + half_h, x0:x0 + half_w]
        big = cv2.resize(tile, (960, 540), interpolation=cv2.INTER_CUBIC)
        for conf, bb in detect(big, 0.15, 0.10, 960):
            fx0, fy0 = bb[0] / 2 + x0, bb[1] / 2 + y0
            fx1, fy1 = bb[2] / 2 + x0, bb[3] / 2 + y0
            cands.append((conf, (fx0, fy0, fx1, fy1)))
    # NMS 合并
    cands.sort(key=lambda c: -c[0])
    kept = []
    for c in cands:
        if all(iou(c[1], k[1]) < 0.45 for k in kept):
            kept.append(c)
    # 尺寸窗
    kept = [c for c in kept if 14 <= (c[1][2] - c[1][0]) <= 400 and 12 <= (c[1][3] - c[1][1]) <= 400]
    return kept

report = {"pos_refined": 0, "pos_kept": 0, "neg_labeled": 0, "frames": {}}
for split in ("train", "val"):
    idir = os.path.join(DS, "images", split)
    ldir = os.path.join(DS, "labels", split)
    for fn in sorted(os.listdir(idir)):
        if not fn.endswith(".jpg"):
            continue
        lp = os.path.join(ldir, fn[:-4] + ".txt")
        has = os.path.isfile(lp) and os.path.getsize(lp) > 0
        img = imread_cn(os.path.join(idir, fn))
        H, W = img.shape[:2]
        kept = tiled_detect(img)
        if has:
            p = open(lp).read().split()
            _, xc, yc, bw, bh = map(float, p)
            ax, ay = xc * W, yc * H
            best = None
            for conf, bb in kept:
                cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
                d = ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5
                if d > 110:
                    continue
                if best is None or conf > best[0]:
                    best = (conf, bb)
            if best:
                bb = best[1]
                pw, ph = (bb[2] - bb[0]) * 0.05, (bb[3] - bb[1]) * 0.05
                bb = (max(0, bb[0] - pw), max(0, bb[1] - ph), min(W, bb[2] + pw), min(H, bb[3] + ph))
                with open(lp, "w", encoding="utf-8") as f:
                    f.write(f"0 {(bb[0]+bb[2])/2/W:.6f} {(bb[1]+bb[3])/2/H:.6f} "
                            f"{(bb[2]-bb[0])/W:.6f} {(bb[3]-bb[1])/H:.6f}\n")
                report["pos_refined"] += 1
                report["frames"][fn[:-4]] = "refined"
            else:
                report["pos_kept"] += 1
                report["frames"][fn[:-4]] = "kept"
        else:
            if kept and kept[0][0] >= 0.25:
                conf, bb = kept[0]
                with open(lp, "w", encoding="utf-8") as f:
                    f.write(f"0 {(bb[0]+bb[2])/2/W:.6f} {(bb[1]+bb[3])/2/H:.6f} "
                            f"{(bb[2]-bb[0])/W:.6f} {(bb[3]-bb[1])/H:.6f}\n")
                report["neg_labeled"] += 1
                report["frames"][fn[:-4]] = f"new {conf:.2f}"

with open(os.path.join(OUT, "tiled_report.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=1)
print(json.dumps({k: v for k, v in report.items() if k != "frames"}, ensure_ascii=False))

# 重渲核验图
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
        os.path.join(OUT, f"final_{n:02d}.jpg"))
print("verification grids:", n)
