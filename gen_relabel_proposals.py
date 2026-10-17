# -*- coding: utf-8 -*-
"""全数据集提议框生成：专属模型 + YOLO-World 低门槛提议 → 裁剪拼图供人工复核"""
import os, json
import numpy as np
import cv2
from ultralytics import YOLO

ROOT = r"e:\全家智能安全调度系统"
DS = os.path.join(ROOT, "datasets", "tello_kinect")
OUT = os.path.join(DS, "relabel_20261017")
os.makedirs(OUT, exist_ok=True)

def imread_cn(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)

def imwrite_cn(p, img, params=None):
    ok, buf = cv2.imencode(os.path.splitext(p)[1], img, params or [])
    if ok:
        buf.tofile(p)

def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0

ded = YOLO(os.path.join(DS, "weights", "tello_yolov8n.pt"))
world = YOLO(os.path.join(ROOT, "yolov8s-worldv2.pt"))
world.set_classes(["toy drone", "drone"])

items = []
for split in ("train", "val"):
    idir = os.path.join(DS, "images", split)
    for f in sorted(os.listdir(idir)):
        if f.endswith(".jpg"):
            lbl = os.path.join(DS, "labels", split, f[:-4] + ".txt")
            has_lbl = os.path.isfile(lbl) and os.path.getsize(lbl) > 0
            items.append((split, f[:-4], has_lbl))

proposals = {}
for idx, (split, name, has_lbl) in enumerate(items):
    img = imread_cn(os.path.join(DS, "images", split, name + ".jpg"))
    H, W = img.shape[:2]
    cands = []  # (box, conf, src)
    r = ded.predict(img, conf=0.08, verbose=False, imgsz=640)[0]
    for b in r.boxes:
        cands.append((tuple(map(float, b.xyxy[0])), float(b.conf), "ded"))
    r = world.predict(img, conf=0.04, verbose=False, imgsz=640)[0]
    for b in r.boxes:
        cands.append((tuple(map(float, b.xyxy[0])), float(b.conf), "world"))
    # NMS 合并
    cands.sort(key=lambda c: -c[1])
    kept = []
    for c in cands:
        if all(iou(c[0], k[0]) < 0.45 for k in kept):
            kept.append(c)
    # 尺寸窗口过滤（像素）：Tello 0.3~4m 在 960 宽下约 12~350px
    kept = [c for c in kept if 12 <= (c[0][2] - c[0][0]) <= 380 and 10 <= (c[0][3] - c[0][1]) <= 380]
    if kept or has_lbl:
        proposals[f"{split}/{name}"] = {"has_lbl": has_lbl, "boxes": [
            {"box": [round(v, 1) for v in c[0]], "conf": round(c[1], 3), "src": c[2]} for c in kept]}

with open(os.path.join(OUT, "proposals.json"), "w", encoding="utf-8") as f:
    json.dump(proposals, f, ensure_ascii=False, indent=1)

# 渲染复核拼图：每张 9 宫格裁剪（裁剪带 40% 边距，缩放到 300px 高）
crops = []
for key, p in proposals.items():
    split, name = key.split("/")
    img = imread_cn(os.path.join(DS, "images", split, name + ".jpg"))
    H, W = img.shape[:2]
    tag = "POS" if p["has_lbl"] else "NEG"
    boxes = p["boxes"] or [{"box": None, "conf": 0, "src": "-"}]
    for j, b in enumerate(p["boxes"][:3]):
        x1, y1, x2, y2 = b["box"]
        mx = (x2 - x1) * 0.4; my = (y2 - y1) * 0.4
        cx1, cy1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
        cx2, cy2 = min(W, int(x2 + mx)), min(H, int(y2 + my))
        crop = img[cy1:cy2, cx1:cx2]
        crop = cv2.resize(crop, (int(crop.shape[1] * 300 / crop.shape[0]), 300))
        cv2.rectangle(crop, (int(x1 - cx1), int(y1 - cy1)), (int(x2 - cx1), int(y2 - cy1)), (0, 0, 255), 2)
        cv2.putText(crop, f"{tag} {b['src']} {b['conf']:.2f}", (4, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.putText(crop, name[-9:], (4, 290), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        crops.append(crop)
    if not p["boxes"]:
        thumb = cv2.resize(img, (int(W * 300 / H), 300))
        cv2.putText(thumb, f"{tag} NO-PROP {name[-9:]}", (4, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        crops.append(thumb)

G = 9
CW, CH = 480, 300  # 统一单元格尺寸，不足处补黑边

def letterbox(img):
    h, w = img.shape[:2]
    s = min(CW / w, CH / h)
    img = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))))
    canvas = np.zeros((CH, CW, 3), np.uint8)
    y0 = (CH - img.shape[0]) // 2; x0 = (CW - img.shape[1]) // 2
    canvas[y0:y0 + img.shape[0], x0:x0 + img.shape[1]] = img
    return canvas

crops = [letterbox(c) for c in crops]
grids = []
for k in range(0, len(crops), G):
    batch = crops[k:k + G]
    while len(batch) < G:
        batch.append(np.zeros((CH, CW, 3), np.uint8))
    rows = [np.hstack(batch[k2:k2 + 3]) for k2 in range(0, G, 3)]
    grids.append(np.vstack(rows))
for gi, g in enumerate(grids, 1):
    imwrite_cn(os.path.join(OUT, f"grid_{gi:02d}.jpg"), g, [cv2.IMWRITE_JPEG_QUALITY, 85])

n_pos = sum(1 for p in proposals.values() if p["has_lbl"])
print(f"总图 {len(items)} | 有提议或有原标签 {len(proposals)} (POS {n_pos} / NEG {len(proposals) - n_pos})")
print(f"拼图 {len(grids)} 张 -> {OUT}")
