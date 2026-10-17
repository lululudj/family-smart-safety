#!/usr/bin/env python3
"""yolo_watcher —— Ganglion 视觉识别进程（笔记本侧，未来可平移到橙派 NPU）

架构：独立进程，喝塔台的 MJPEG 视频流，跑 YOLOv8n，把检测结果回传塔台。
塔台侧零改动即可换识别器（笔记本 YOLO → NPU RKNN → 任意实现）。

用法：
    python yolo_watcher.py            # 前台运行，Ctrl+C 停止
前提：Tello 已开机、页面已点「开启视频」（视频流在跑）
"""
import os
import sys
import time
import urllib.request

import cv2
import numpy as np
import requests
from ultralytics import YOLO

TOWER = "http://127.0.0.1:8080"  # 塔台已迁到本机
STREAM = f"{TOWER}/video/mjpg"
REPORT = f"{TOWER}/api/vision/detections"
MODEL_NAME = "yolov8n.pt"
# GitHub 被墙时的权重镜像兜底
MIRROR = "https://ghfast.top/https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"
INTERVAL = 0.2  # 检测频率 5Hz（够用且省资源）


def load_model() -> YOLO:
    if os.path.exists(MODEL_NAME):
        return YOLO(MODEL_NAME)
    try:
        return YOLO(MODEL_NAME)  # 首次会尝试从 GitHub 下载
    except Exception as e:
        print(f"[model] 官方下载失败（{e}），改走镜像 ...")
        urllib.request.urlretrieve(MIRROR, MODEL_NAME)
        return YOLO(MODEL_NAME)


def mjpeg_frames(url: str, timeout: int = 8):
    """从 MJPEG 流持续产出 BGR 帧（断流自动重连）"""
    while True:
        try:
            r = urllib.request.urlopen(url, timeout=timeout)
            print("[stream] 已连接视频流")
            buf = b""
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    s = buf.find(b"\xff\xd8")  # JPEG SOI
                    if s < 0:
                        buf = b"" if len(buf) > (1 << 20) else buf
                        break
                    e = buf.find(b"\xff\xd9", s)  # JPEG EOI
                    if e < 0:
                        buf = buf[s:]
                        if len(buf) > (2 << 20):
                            buf = b""
                        break
                    jpg = buf[s:e + 2]
                    buf = buf[e + 2:]
                    if len(jpg) < 1000:
                        continue  # 噪声碎片
                    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        yield img
        except Exception as e:
            print(f"[stream] {e} —— 3 秒后重试（确认无人机在线、页面已开启视频）")
            time.sleep(3)


def main():
    print("加载 YOLOv8n 模型 ...")
    model = load_model()
    print("模型就绪，开始监听塔台视频流")
    last = 0.0
    frames = 0
    for frame in mjpeg_frames(STREAM):
        now = time.time()
        if now - last < INTERVAL:
            continue
        last = now
        frames += 1
        results = model(frame, verbose=False, imgsz=640, device="cpu")
        objs = []
        for r in results:
            for box in r.boxes:
                cls = int(box.cls[0])
                conf = float(box.conf[0])
                objs.append({"label": r.names.get(cls, str(cls)), "conf": conf})
        try:
            requests.post(REPORT, json={"source": "laptop-yolov8n", "objects": objs}, timeout=3)
        except Exception as e:
            print("[report]", e)
        if frames % 25 == 0:  # 每 5 秒打一条状态
            people = sum(1 for o in objs if o["label"] == "person")
            print(f"{time.strftime('%H:%M:%S')} 帧数 {frames} | 本帧对象 {len(objs)} | 人员 {people}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止")
        sys.exit(0)

