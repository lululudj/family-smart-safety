# -*- coding: utf-8 -*-
"""自举第2步：用干净种子训练模型（exports/tello_n_seed，不覆盖线上权重）"""
import os, csv, time
import torch
from ultralytics import YOLO

ROOT = r"e:\全家智能安全调度系统"
DS = ROOT + r"\datasets\tello_kinect"

def main():
    model = YOLO(ROOT + r"\yolov8n.pt")
    device = 0 if torch.cuda.is_available() else "cpu"
    print("train device:", device, flush=True)
    t0 = time.time()
    model.train(
        data=DS + r"\tello.yaml",
        epochs=40,
        imgsz=640,
        batch=8,
        workers=2,
        device=device,
        project=DS + r"\exports",
        name="tello_n_seed",
        exist_ok=True,
        patience=15,
        verbose=True,
    )
    print(f"elapsed {time.time()-t0:.0f}s", flush=True)
    csv_path = DS + r"\exports\tello_n_seed\results.csv"
    if os.path.isfile(csv_path):
        rows = list(csv.DictReader(open(csv_path)))
        last = rows[-1]
        for k in ("metrics/precision(B)", "metrics/recall(B)", "metrics/mAP50(B)", "metrics/mAP50-95(B)"):
            print(k, last.get(k))

if __name__ == "__main__":
    main()
