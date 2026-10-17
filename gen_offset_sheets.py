# -*- coding: utf-8 -*-
"""偏移校正第1步：按时间聚类正样本组，每组渲染框中心特写（十字标出框中心），供读偏移。"""
import os, json
import numpy as np
import cv2

ROOT = r"e:\全家智能安全调度系统"
DS = os.path.join(ROOT, "datasets", "tello_kinect")
OUT = os.path.join(DS, "relabel_20261017")

def imread_cn(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)

def imwrite_cn(p, img, q=90):
    cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tofile(p)

def tsec(name):
    import re
    m = re.search(r"(\d\d)(\d\d)(\d\d)_(\d+)", name)
    hh, mm, ss, ms = map(int, m.groups())
    return hh * 3600 + mm * 60 + ss + ms / 1000.0

# 收集正样本
pos = []
for split in ("train", "val"):
    idir = os.path.join(DS, "images", split)
    for fn in sorted(os.listdir(idir)):
        if not fn.endswith(".jpg"):
            continue
        lp = os.path.join(DS, "labels", split, fn[:-4] + ".txt")
        if not (os.path.isfile(lp) and os.path.getsize(lp) > 0):
            continue
        p = open(lp).read().split()
        _, xc, yc, bw, bh = map(float, p)
        pos.append({"split": split, "fn": fn, "t": tsec(fn),
                    "box": ((xc - bw / 2) * 960, (yc - bh / 2) * 540,
                            (xc + bw / 2) * 960, (yc + bh / 2) * 540)})
pos.sort(key=lambda d: d["t"])

# 时间聚类：间隔<=2.5s 分同组
groups, cur = [], [pos[0]]
for d in pos[1:]:
    if d["t"] - cur[-1]["t"] <= 2.5:
        cur.append(d)
    else:
        groups.append(cur)
        cur = [d]
groups.append(cur)
print(f"{len(pos)} pos -> {len(groups)} groups")

# 每组取中间帧渲染特写：框中心十字 + 20px网格
cells, meta = [], []
for gi, g in enumerate(groups):
    d = g[len(g) // 2]
    img = imread_cn(os.path.join(DS, "images", d["split"], d["fn"]))
    x1, y1, x2, y2 = [int(v) for v in d["box"]]
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    R = 110
    X1, Y1 = max(0, cx - R), max(0, cy - R)
    X2, Y2 = min(960, cx + R), min(540, cy + R)
    crop = img[Y1:Y2, X1:X2].copy()
    # 网格（绝对坐标对齐 20px）
    for gx in range((X1 // 20 + 1) * 20, X2, 20):
        cv2.line(crop, (gx - X1, 0), (gx - X1, Y2 - Y1), (80, 80, 80), 1)
    for gy in range((Y1 // 20 + 1) * 20, Y2, 20):
        cv2.line(crop, (0, gy - Y1), (X2 - X1, gy - Y1), (80, 80, 80), 1)
    for gx in range((X1 // 100 + 1) * 100, X2, 100):
        cv2.line(crop, (gx - X1, 0), (gx - X1, Y2 - Y1), (60, 60, 200), 1)
        cv2.putText(crop, str(gx), (gx - X1 + 1, 11), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
    for gy in range((Y1 // 100 + 1) * 100, Y2, 100):
        cv2.line(crop, (0, gy - Y1), (X2 - X1, gy - Y1), (60, 60, 200), 1)
        cv2.putText(crop, str(gy), (1, gy - Y1 - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
    cv2.rectangle(crop, (x1 - X1, y1 - Y1), (x2 - X1, y2 - Y1), (0, 0, 255), 2)
    cv2.drawMarker(crop, (cx - X1, cy - Y1), (0, 255, 0), cv2.MARKER_CROSS, 16, 2)
    crop = cv2.resize(crop, (420, 420), interpolation=cv2.INTER_NEAREST)
    cv2.putText(crop, f"g{gi}#{len(g)} {d['fn'][-13:]}", (4, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
    cells.append(crop)
    meta.append({"gi": gi, "rep": d["fn"][:-4], "members": [x["fn"][:-4] for x in g]})

with open(os.path.join(OUT, "groups.json"), "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=1)

# 拼 sheet：4 格/张（2x2）
n = 0
for k in range(0, len(cells), 4):
    b = cells[k:k + 4]
    while len(b) < 4:
        b.append(np.zeros((420, 420, 3), np.uint8))
    n += 1
    imwrite_cn(os.path.join(OUT, f"offadj_{n:02d}.jpg"), np.vstack([np.hstack(b[:2]), np.hstack(b[2:])]))
print(f"offadj sheets: {n}")
