# -*- coding: utf-8 -*-
"""框精修：以现有人工框为锚，在扩大裁剪上跑模型重新定位，IoU 锚定选优。"""
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

def center_dist(a, b):
    ca = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
    cb = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    return ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5

ded = YOLO(os.path.join(DS, "weights", "tello_yolov8n.pt"))
world = YOLO(os.path.join(ROOT, "yolov8s-worldv2.pt"))
world.set_classes(["toy drone", "drone"])
device = "cuda:0" if torch.cuda.is_available() else "cpu"
print("refine device:", device)

MARGIN = 0.9      # 锚框每边扩 90%
MAX_DIST = 130.0  # 候选中心距锚中心上限

report = {"refined": 0, "kept": 0, "total": 0, "frames": {}}
for split in ("train", "val"):
    idir = os.path.join(DS, "images", split)
    ldir = os.path.join(DS, "labels", split)
    for fn in sorted(os.listdir(idir)):
        if not fn.endswith(".jpg"):
            continue
        lp = os.path.join(ldir, fn[:-4] + ".txt")
        if not (os.path.isfile(lp) and os.path.getsize(lp) > 0):
            continue
        line = open(lp, encoding="utf-8").read().strip().split()
        _, xc, yc, bw, bh = map(float, line)
        img = imread_cn(os.path.join(idir, fn))
        H, W = img.shape[:2]
        x1, y1 = int((xc - bw / 2) * W), int((yc - bh / 2) * H)
        x2, y2 = int((xc + bw / 2) * W), int((yc + bh / 2) * H)
        anchor = (x1, y1, x2, y2)
        mw, mh = int((x2 - x1) * MARGIN), int((y2 - y1) * MARGIN)
        cx1, cy1 = max(0, x1 - mw), max(0, y1 - mh)
        cx2, cy2 = min(W, x2 + mw), min(H, y2 + mh)
        crop = img[cy1:cy2, cx1:cx2]
        cands = []
        with torch.no_grad():
            r = ded.predict(crop, conf=0.05, verbose=False, imgsz=480, device=device)[0]
            for b in r.boxes:
                bb = tuple(float(v) for v in b.xyxy[0])
                cands.append((float(b.conf), bb))
            r = world.predict(crop, conf=0.04, verbose=False, imgsz=480, device=device)[0]
            for b in r.boxes:
                bb = tuple(float(v) for v in b.xyxy[0])
                cands.append((float(b.conf) * 0.9, bb))
        # 映射回全图坐标，并按锚约束过滤
        best = None
        for conf, bb in cands:
            fb = (bb[0] + cx1, bb[1] + cy1, bb[2] + cx1, bb[3] + cy1)
            if center_dist(fb, anchor) > MAX_DIST:
                continue
            ov = iou(fb, anchor)
            score = conf + ov  # 与锚重叠越高越可信
            if best is None or score > best[0]:
                best = (score, fb, conf, ov)
        if best and best[3] > 0.05:
            fb = best[1]
            # 稍微外扩 4%，容纳桨护边缘
            pw, ph = (fb[2] - fb[0]) * 0.04, (fb[3] - fb[1]) * 0.04
            fb = (max(0, fb[0] - pw), max(0, fb[1] - ph),
                  min(W, fb[2] + pw), min(H, fb[3] + ph))
            nxc, nyc = (fb[0] + fb[2]) / 2 / W, (fb[1] + fb[3]) / 2 / H
            nbw, nbh = (fb[2] - fb[0]) / W, (fb[3] - fb[1]) / H
            with open(lp, "w", encoding="utf-8") as f:
                f.write(f"0 {nxc:.6f} {nyc:.6f} {nbw:.6f} {nbh:.6f}\n")
            report["refined"] += 1
            report["frames"][fn[:-4]] = {"old": anchor, "new": [round(v, 1) for v in fb]}
        else:
            report["kept"] += 1
        report["total"] += 1

with open(os.path.join(OUT, "refine_report.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=1)
print(f"total={report['total']} refined={report['refined']} kept={report['kept']}")

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
