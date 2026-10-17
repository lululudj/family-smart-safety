# -*- coding: utf-8 -*-
"""YOLO-World prompt 调优：哪个描述短语对无人机的响应最强。

YOLO-World 的文本嵌入对措辞敏感，同一架无人机不同短语分差可达 2-3 倍。
用生成图当测试基准（domain gap 存在，但相对排序是可信的）。
"""
import os

import cv2
import numpy as np
import torch
from ultralytics import YOLO

ROOT = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(ROOT, "test_drone_images",
                   "photorealistic_photo_of_a_smal_2026-10-15T09-46-20.png")
MODEL = os.path.join(ROOT, "yolov8s-worldv2.pt")

# 每组 = 一套 set_classes 类别表（drone 相关短语放最后，索引即可区分）
VARIANTS = {
    "A: drone + quadcopter drone": ["person", "chair", "tv", "drone", "quadcopter drone"],
    "B: drone(单类)": ["person", "chair", "tv", "drone"],
    "C: 四桨描述": ["person", "chair", "tv",
                    "small quadcopter with four propellers"],
    "D: 飞行相机无人机": ["person", "chair", "tv", "flying camera drone"],
    "E: 玩具无人机": ["person", "chair", "tv", "toy drone"],
    "F: 大疆Tello": ["person", "chair", "tv", "tello drone"],
    "G: 组合拳(4个同义词)": ["person", "chair", "tv", "drone",
                             "quadcopter", "flying drone", "camera drone"],
    "H: 完整生产类别表": ["person", "chair", "keyboard", "laptop", "tv",
                          "cell phone", "bottle", "cup", "book", "backpack",
                          "drone", "quadcopter drone"],
}

img = cv2.imdecode(np.fromfile(IMG, dtype=np.uint8), cv2.IMREAD_COLOR)

results_summary = []
for name, classes in VARIANTS.items():
    model = YOLO(MODEL)
    model.set_classes(classes)
    drone_hits = []
    with torch.no_grad():
        preds = model(img, verbose=False, imgsz=640, device="cuda:0", conf=0.01)
    for r in preds:
        for b in r.boxes:
            label = r.names.get(int(b.cls[0]), "?")
            if any(k in label for k in ("drone", "quad", "tello")):
                drone_hits.append((float(b.conf[0]), label))
    top = max(drone_hits)[0] if drone_hits else 0.0
    best_label = max(drone_hits)[1] if drone_hits else "-"
    results_summary.append((top, name, best_label))
    print(f"  {name:<28} drone 最高 conf = {top:.3f}  ({best_label})")
    del model
    torch.cuda.empty_cache()

print("\n排名（高 → 低）：")
for i, (conf, name, label) in enumerate(sorted(results_summary, reverse=True), 1):
    print(f"  {i}. {name:<28} {conf:.3f}")
