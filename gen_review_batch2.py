# -*- coding: utf-8 -*-
"""生成复核材料：
1) poszoom_*.jpg —— 全部正样本框±80px 2x放大，供逐个裁决
2) fngrid_*.jpg  —— 已确认假阴性但无提议的帧，画 40px 坐标网格供手动补框
"""
import os, json
import numpy as np
import cv2

ROOT = r"e:\全家智能安全调度系统"
DS = os.path.join(ROOT, "datasets", "tello_kinect")
OUT = os.path.join(DS, "relabel_20261017")

def imread_cn(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)

def imwrite_cn(p, img, q=88):
    cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tofile(p)

def yolo_xyxy(l, W, H):
    _, xc, yc, w, h = map(float, l.split())
    return int((xc - w / 2) * W), int((yc - h / 2) * H), int((xc + w / 2) * W), int((yc + h / 2) * H)

def cell(img, short):
    canvas = np.zeros((420, 420, 3), np.uint8)
    s = min(1.0, 420 / max(img.shape[:2]))
    r = cv2.resize(img, (int(img.shape[1] * s), int(img.shape[0] * s)))
    y0 = (420 - r.shape[0]) // 2; x0 = (420 - r.shape[1]) // 2
    canvas[y0:y0 + r.shape[0], x0:x0 + r.shape[1]] = r
    cv2.putText(canvas, short, (4, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    return canvas

def grids_of(cells, prefix, per=9):
    n = 0
    for k in range(0, len(cells), per):
        b = cells[k:k + per]
        while len(b) < per or len(b) % 3:
            b.append(np.zeros((420, 420, 3), np.uint8))
        rows = [np.hstack(b[i:i + 3]) for i in range(0, len(b), 3)]
        n += 1
        imwrite_cn(os.path.join(OUT, f"{prefix}_{n:02d}.jpg"), np.vstack(rows))
    return n

# ---- 1) 全部正样本放大 ----
props = json.load(open(os.path.join(OUT, "proposals.json"), encoding="utf-8"))
cells = []
for key, p in sorted(props.items()):
    if not p["has_lbl"]:
        continue
    split, name = key.split("/")
    img = imread_cn(os.path.join(DS, "images", split, name + ".jpg"))
    H, W = img.shape[:2]
    lbl = os.path.join(DS, "labels", split, name + ".txt")
    m = 80
    for line in open(lbl, encoding="utf-8"):
        if line.strip():
            x1, y1, x2, y2 = yolo_xyxy(line, W, H)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 1)
            cx1, cy1 = max(0, x1 - m), max(0, y1 - m)
            crop = img[cy1:min(H, y2 + m), cx1:min(W, x2 + m)]
            s = 2.2 if max(crop.shape[:2]) < 200 else 1.5
            crop = cv2.resize(crop, (int(crop.shape[1] * s), int(crop.shape[0] * s)),
                              interpolation=cv2.INTER_NEAREST)
            cells.append(cell(crop, name[-9:]))
print("poszoom cells:", len(cells))
print("poszoom grids:", grids_of(cells, "poszoom"))

# ---- 2) 假阴性无提议帧：坐标网格 ----
FN_NOPROP = [
    ("train", "130241_930"), ("train", "130504_362"), ("train", "130529_778"),
    ("train", "130626_917"), ("train", "130131_848"), ("train", "130613_800"),
    ("val", "130542_767"),
]
cells = []
for split, short in FN_NOPROP:
    img = imread_cn(os.path.join(DS, "images", split, "20261017_" + short + ".jpg"))
    H, W = img.shape[:2]
    for x in range(0, W, 40):
        cv2.line(img, (x, 0), (x, H), (60, 60, 60), 1)
    for y in range(0, H, 40):
        cv2.line(img, (0, y), (W, y), (60, 60, 60), 1)
    for x in range(0, W, 200):
        cv2.putText(img, str(x), (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
    for y in range(0, H, 200):
        cv2.putText(img, str(y), (2, y + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
    cells.append(cell(cv2.resize(img, (760, 428)), short))
print("fngrid grids:", grids_of(cells, "fngrid", per=4))
