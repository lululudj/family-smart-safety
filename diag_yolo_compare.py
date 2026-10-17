# -*- coding: utf-8 -*-
"""诊断：专属权重 vs YOLO-World 对当前 Kinect 帧里 Tello 的检出能力。

回答"识别不准到底是谁的问题"，一帧定胜负，不做任何后处理 / 尺寸过滤：
  A 专属权重 datasets/tello_kinect/weights/tello_yolov8n.pt（当前线上用的）
  B 开放词汇 yolov8s-worldv2.pt（多提示词）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kinect_yolo3d as k            # noqa: E402
from ultralytics import YOLO         # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))

color = k.read_color()
meta = k.read_meta()
print(f"帧尺寸: {None if color is None else color.shape}  "
      f"深度网格 {meta['width']}x{meta['height']} "
      f"fx={float(meta['fx']):.0f} fy={float(meta['fy']):.0f}")

print("\n=== A 专属权重（当前线上用的）")
p = k.TELLO_DEDICATED_WT
print(f"    {p}")
if os.path.exists(p):
    ma = YOLO(p)
    ra = ma.predict(color, conf=0.01, verbose=False)[0]
    print(f"    类别表: {ma.names}")
    n = 0
    for b in ra.boxes:
        n += 1
        print(f"      {ma.names[int(b.cls)]:<20} conf={float(b.conf):.3f} "
              f"box={[round(x, 1) for x in b.xyxy[0].tolist()]}")
    print(f"    共 {n} 个框（conf>=0.01）")
else:
    print("    权重缺失")

print("\n=== B YOLO-World 开放词汇")
mb = YOLO(os.path.join(ROOT, "yolov8s-worldv2.pt"))
mb.set_classes(k.WORLD_CLASSES)
rb = mb.predict(color, conf=0.03, verbose=False)[0]
n = 0
for b in rb.boxes:
    n += 1
    print(f"      {mb.names[int(b.cls)]:<20} conf={float(b.conf):.3f} "
          f"box={[round(x, 1) for x in b.xyxy[0].tolist()]}")
print(f"    共 {n} 个框（conf>=0.03）")
