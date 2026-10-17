# -*- coding: utf-8 -*-
"""诊断：在生成图上把 conf 门槛压到 0.01，看 YOLO-World 到底给了什么分数"""
import os

import cv2
import numpy as np
import torch
from ultralytics import YOLO

ROOT = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(ROOT, "test_drone_images",
                   "photorealistic_photo_of_a_smal_2026-10-15T09-46-20.png")

WORLD_CLASSES = [
    "person", "chair", "keyboard", "laptop", "tv", "cell phone",
    "bottle", "cup", "book", "backpack",
    "drone", "quadcopter drone",
]

model = YOLO(os.path.join(ROOT, "yolov8s-worldv2.pt"))
model.set_classes(WORLD_CLASSES)

img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)
print(f"图像 {img.shape[1]}x{img.shape[0]}")

with torch.no_grad():
    results = model(img, verbose=False, imgsz=640, device="cuda:0", conf=0.01)

found = []
for r in results:
    for b in r.boxes:
        label = r.names.get(int(b.cls[0]), "?")
        conf = float(b.conf[0])
        x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
        found.append((conf, label, (x1, y1, x2, y2)))

found.sort(reverse=True)
print(f"\n全部候选 {len(found)} 个（按分数降序，只列前 20）：")
for conf, label, box in found[:20]:
    print(f"  {label:<18} {conf:.3f}   box=({box[0]:.0f},{box[1]:.0f},{box[2]:.0f},{box[3]:.0f})")

drone_cands = [(c, l, b) for c, l, b in found if "drone" in l or "quad" in l]
if drone_cands:
    top = drone_cands[0]
    print(f"\n最高 drone 候选: {top[1]} conf={top[0]:.3f}")
else:
    print("\n没有任何 drone 类候选 —— 生成图上模型完全没往无人机方向想")
