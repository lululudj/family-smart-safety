# -*- coding: utf-8 -*-
"""Train YOLOv8n single-class tello -> weights/tello_yolov8n_score.pt"""
import os, csv, time, shutil
from pathlib import Path
import torch
from ultralytics import YOLO

ROOT = Path(r"E:\全家智能安全调度系统")
DS = ROOT / "datasets" / "tello_kinect"
OUT_NAME = "tello_n_score"
EXPORT = DS / "exports" / OUT_NAME
WT_DIR = DS / "weights"
WT_DIR.mkdir(parents=True, exist_ok=True)

def main():
    model = YOLO(str(ROOT / "yolov8n.pt"))
    device = 0 if torch.cuda.is_available() else "cpu"
    print("train device:", device, flush=True)
    t0 = time.time()
    model.train(
        data=str(DS / "tello.yaml"),
        epochs=50,
        imgsz=640,
        batch=8,
        workers=2,
        device=device,
        project=str(DS / "exports"),
        name=OUT_NAME,
        exist_ok=True,
        patience=20,
        verbose=True,
    )
    print(f"elapsed {time.time()-t0:.0f}s", flush=True)
    best = EXPORT / "weights" / "best.pt"
    last = EXPORT / "weights" / "last.pt"
    dest = WT_DIR / "tello_yolov8n_score.pt"
    src = best if best.is_file() else last
    if not src.is_file():
        raise SystemExit(f"no weights at {EXPORT}/weights")
    shutil.copy2(src, dest)
    print(f"copied {src} -> {dest}", flush=True)
    csv_path = EXPORT / "results.csv"
    metrics = {}
    if csv_path.is_file():
        rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
        last_row = rows[-1]
        for k in ("epoch", "metrics/precision(B)", "metrics/recall(B)",
                  "metrics/mAP50(B)", "metrics/mAP50-95(B)"):
            metrics[k] = last_row.get(k)
            print(k, last_row.get(k), flush=True)
        # also best mAP50 across rows
        best_map = 0.0
        best_ep = None
        for r in rows:
            try:
                v = float(r.get("metrics/mAP50(B)") or 0)
            except ValueError:
                v = 0.0
            if v >= best_map:
                best_map = v
                best_ep = r.get("epoch")
        print(f"best_mAP50={best_map} @epoch={best_ep}", flush=True)
        metrics["best_mAP50"] = best_map
        metrics["best_epoch"] = best_ep
    (WT_DIR / "tello_yolov8n_score_metrics.txt").write_text(
        "\n".join(f"{k}={v}" for k,v in metrics.items()), encoding="utf-8")

if __name__ == "__main__":
    main()
