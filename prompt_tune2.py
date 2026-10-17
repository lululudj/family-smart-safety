# -*- coding: utf-8 -*-
"""验证：完整生产类别表（常规10类 + toy drone）下的无人机分数是否保得住"""
import os

import cv2
import numpy as np
import torch
from ultralytics import YOLO

ROOT = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(ROOT, "test_drone_images",
                   "photorealistic_photo_of_a_smal_2026-10-15T09-46-20.png")
MODEL = os.path.join(ROOT, "yolov8s-worldv2.pt")

CANDIDATES = {
    "现行(quadcopter drone)": [
        "person", "chair", "keyboard", "laptop", "tv", "cell phone",
        "bottle", "cup", "book", "backpack", "drone", "quadcopter drone"],
    "换成 toy drone": [
        "person", "chair", "keyboard", "laptop", "tv", "cell phone",
        "bottle", "cup", "book", "backpack", "toy drone"],
    "toy drone + drone 双保险": [
        "person", "chair", "keyboard", "laptop", "tv", "cell phone",
        "bottle", "cup", "book", "backpack", "toy drone", "drone"],
}

img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)

for name, classes in CANDIDATES.items():
    model = YOLO(MODEL)
    model.set_classes(classes)
    hits = []
    with torch.no_grad():
        preds = model(img, verbose=False, imgsz=640, device="cuda:0", conf=0.01)
    for r in preds:
        for b in r.boxes:
            label = r.names.get(int(b.cls[0]), "?")
            if "drone" in label or "quad" in label or "toy" in label:
                hits.append((float(b.conf[0]), label, tuple(round(float(v)) for v in b.xyxy[0])))
    if hits:
        c, l, box = max(hits)
        w_px = box[2] - box[0]
        phys = w_px * 1.5 / 851.5  # 常数深度 1.5m、合成 fx=851.5
        print(f"  {name:<26} 最高 = {l}  conf={c:.3f}  box宽{w_px}px -> 物理宽{phys:.2f}m")
    else:
        print(f"  {name:<26} 无 drone 候选")
    del model
    torch.cuda.empty_cache()
