# -*- coding: utf-8 -*-
"""用专属模型 + YOLO-World 对用户实拍的 Tello 照片做识别摸底"""
import os
import numpy as np
import cv2
from ultralytics import YOLO

ROOT = r"e:\全家智能安全调度系统"
PHOTOS = r"c:\Users\Administrator\.trae-cn\attachments"
OUT = os.path.join(ROOT, "datasets", "tello_kinect", "review_20261017")

photos = [
    "c7474351-0946-48cc-89d0-97e59b7cd879_99e1242e-cd76-48cd-954d-c42fc7ed626e_image.png",
    "fc9566dc-09a4-48f6-8dbc-7d82147f0776_225b6504-af5f-4627-83be-3b7e909708c2_image.png",
    "c19fdd91-2cb1-4a21-918f-a41619b42319_950cd527-a4e0-42a8-9762-e6511ea82dd2_image.png",
    "62dfd1d0-c3fd-4f9d-b8e3-2d27897d28df_7e0fd823-5a38-4da9-9640-897053c1f7d3_image.png",
    "37eb3122-8616-403f-9332-be5d242aad99_a505748d-8058-4d52-ac0d-fd791a23d940_image.png",
    "a31b38df-d891-4e6d-8e43-2bfa11df0a78_4eb1abbd-74fc-4477-8c5f-c8cc447ffeb0_image.png",
]

def imread_cn(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)

def imwrite_cn(p, img, params=None):
    ok, buf = cv2.imencode(os.path.splitext(p)[1], img, params or [])
    if ok:
        buf.tofile(p)

ded = YOLO(os.path.join(ROOT, "datasets", "tello_kinect", "weights", "tello_yolov8n.pt"))
world = YOLO(os.path.join(ROOT, "yolov8s-worldv2.pt"))
world.set_classes(["toy drone", "drone", "remote control"])

report = []
tiles = []
for i, fn in enumerate(photos, 1):
    img = imread_cn(os.path.join(PHOTOS, fn))
    H, W = img.shape[:2]
    scale = 640 / W
    img_s = cv2.resize(img, (640, int(H * scale)))
    r_ded = ded.predict(img_s, conf=0.10, verbose=False)[0]
    r_wld = world.predict(img_s, conf=0.05, verbose=False)[0]
    d_conf = max((float(b.conf) for b in r_ded.boxes), default=0.0)
    w_conf = max((float(b.conf) for b in r_wld.boxes if int(b.cls) in (0, 1)), default=0.0)
    report.append((i, d_conf, w_conf))
    for b in r_ded.boxes:
        x1, y1, x2, y2 = map(int, b.xyxy[0])
        cv2.rectangle(img_s, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(img_s, f"ded {float(b.conf):.2f}", (x1, max(16, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    for b in r_wld.boxes:
        if int(b.cls) not in (0, 1):
            continue
        x1, y1, x2, y2 = map(int, b.xyxy[0])
        cv2.rectangle(img_s, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(img_s, f"world {float(b.conf):.2f}", (x1, min(img_s.shape[0] - 6, y2 + 18)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.putText(img_s, f"#{i}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
    tiles.append(img_s)

rows = [np.hstack(tiles[k:k + 2]) for k in range(0, 6, 2)]
grid = np.vstack(rows)
imwrite_cn(os.path.join(OUT, "_photo_test.jpg"), grid, [cv2.IMWRITE_JPEG_QUALITY, 88])

print("照片 | 专属模型conf | YOLO-World conf")
for i, d, w in report:
    print(f"#{i}  |  {d:.3f}  |  {w:.3f}")
print("拼图:", os.path.join(OUT, "_photo_test.jpg"))
