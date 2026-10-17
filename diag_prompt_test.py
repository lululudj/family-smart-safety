# -*- coding: utf-8 -*-
"""拿「飞行中的 Tello」这一帧，测不同 YOLO-World 提示词 / 输入分辨率的检出能力。

飞行帧来自 kinect_runtime/debug_nodrone.jpg（无可用位姿时自动存的）。
背景：现有提示词在地面能检出（conf 0.05~0.21），但一飞起来就完全无输出。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import numpy as np
from ultralytics import YOLO

ROOT = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(ROOT, "tower_ui", "kinect_runtime", "debug_nodrone.jpg")

data = np.fromfile(IMG, dtype=np.uint8)
frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
print("帧:", None if frame is None else frame.shape)

PROMPT_SETS = {
    "现有": ["toy drone", "drone", "tello", "DJI Tello", "white quadcopter",
             "small drone", "mini drone", "quadcopter", "white toy drone",
             "white drone", "small white drone", "handheld drone"],
    "飞行向": ["flying drone", "drone in flight", "flying quadcopter",
               "white drone flying", "hovering drone", "quadcopter in the air"],
    "颜色形状": ["white quadcopter", "white cross shaped object",
                 "small white flying object", "toy quadcopter", "small white drone"],
    "极简": ["drone", "quadcopter"],
}

m = YOLO(os.path.join(ROOT, "yolov8s-worldv2.pt"))
for imgsz in (640, 1280):
    print(f"\n########## imgsz={imgsz} ##########")
    for name, prompts in PROMPT_SETS.items():
        m.set_classes(prompts)
        r = m.predict(frame, conf=0.005, imgsz=imgsz, verbose=False)[0]
        print(f"=== {name}（{len(prompts)} 词）")
        if not len(r.boxes):
            print("    无输出")
            continue
        for b in r.boxes:
            print(f"    {r.names[int(b.cls[0])]:<30} conf={float(b.conf[0]):.4f} "
                  f"box={[round(v, 1) for v in b.xyxy[0].tolist()]}")
