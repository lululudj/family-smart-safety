# -*- coding: utf-8 -*-
"""用生成图离线验证：YOLO-World 新类别表能否检出无人机 + 尺寸校验是否工作。

做法：伪造一个 KINECT_RUNTIME 目录（生成图当彩图、常数 1.5m 当深度、
按 84.1 度 FOV 推算内参），然后以 --probe 模式跑 kinect_yolo3d.py。
不碰真实 kinect_runtime，不影响在线链路。
"""
import json
import os
import shutil
import subprocess
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_IMG = os.path.join(ROOT, "test_drone_images",
                       "photorealistic_photo_of_a_smal_2026-10-15T09-46-20.png")
TEST_DIR = os.path.join(ROOT, "test_kinect_runtime")
PY = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"

W, H, Z_MM = 1536, 1024, 1500  # 画布与常数深度 1.5m
FOV_DEG = 84.1
FX = (W / 2) / __import__("math").tan(__import__("math").radians(FOV_DEG / 2))

os.makedirs(TEST_DIR, exist_ok=True)

img = cv2.imdecode(np.fromfile(SRC_IMG, dtype=np.uint8), cv2.IMREAD_COLOR)
assert img is not None, f"读不到生成图: {SRC_IMG}"
img = cv2.resize(img, (W, H))
ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
buf.tofile(os.path.join(TEST_DIR, "color.jpg"))  # tofile 规避中文路径

depth = np.full((H, W), Z_MM, dtype="<u2")
depth.tofile(os.path.join(TEST_DIR, "depth_raw.bin"))

meta = {"width": W, "height": H, "unit": "mm", "source": "synthetic",
        "fx": round(FX, 3), "fy": round(FX, 3),
        "cx": W / 2, "cy": H / 2, "updated_at": "test"}
json.dump(meta, open(os.path.join(TEST_DIR, "depth_meta.json"), "w", encoding="utf-8"))

print(f"测试目录就绪: {TEST_DIR}")
print(f"合成内参 fx={FX:.1f}（按 {FOV_DEG} 度 FOV 推算），常数深度 {Z_MM}mm")

env = dict(os.environ, KINECT_DIR=TEST_DIR, PYTHONIOENCODING="utf-8")
r = subprocess.run([PY, "-u", os.path.join(ROOT, "kinect_yolo3d.py"), "--probe"],
                   capture_output=True, env=env, timeout=180)
out = r.stdout.decode("utf-8", "ignore") + r.stderr.decode("utf-8", "ignore")
print("-" * 64)
print(out)
print("-" * 64)

# 把标注图搬到项目根方便查看
probe_out = os.path.join(os.environ.get("TEMP", "."), "k3d_probe_annotated.jpg")
if os.path.exists(probe_out):
    shutil.copyfile(probe_out, os.path.join(ROOT, "_drone_probe_annotated.jpg"))
    print("标注图已保存: _drone_probe_annotated.jpg")
