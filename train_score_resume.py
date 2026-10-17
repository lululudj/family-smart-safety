# -*- coding: utf-8 -*-
"""Resume score training then export tello_yolov8n_score.pt"""
import os, csv, time, shutil
from pathlib import Path
import torch
from ultralytics import YOLO

ROOT = Path(r"E:\全家智能安全调度系统")
DS = ROOT / "datasets" / "tello_kinect"
EXPORT = DS / "exports" / "tello_n_score"
LAST = EXPORT / "weights" / "last.pt"
BEST = EXPORT / "weights" / "best.pt"
DEST = DS / "weights" / "tello_yolov8n_score.pt"

def dump_metrics():
    csv_path = EXPORT / "results.csv"
    metrics = {}
    if csv_path.is_file():
        rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
        last_row = rows[-1]
        for k in ("epoch", "metrics/precision(B)", "metrics/recall(B)",
                  "metrics/mAP50(B)", "metrics/mAP50-95(B)"):
            metrics[k] = last_row.get(k)
            print(k, last_row.get(k), flush=True)
        best_map, best_ep = 0.0, None
        for r in rows:
            try:
                v = float(r.get("metrics/mAP50(B)") or 0)
            except ValueError:
                v = 0.0
            if v >= best_map:
                best_map, best_ep = v, r.get("epoch")
        print(f"best_mAP50={best_map} @epoch={best_ep}", flush=True)
        metrics["best_mAP50"] = best_map
        metrics["best_epoch"] = best_ep
    (DS / "weights" / "tello_yolov8n_score_metrics.txt").write_text(
        "\n".join(f"{k}={v}" for k, v in metrics.items()), encoding="utf-8")
    return metrics

def main():
    device = 0 if torch.cuda.is_available() else "cpu"
    print("resume device:", device, "last=", LAST.is_file(), flush=True)
    if LAST.is_file():
        model = YOLO(str(LAST))
        t0 = time.time()
        try:
            model.train(resume=True)
        except Exception as e:
            print("resume failed:", e, flush=True)
            print("continuing with current best.pt", flush=True)
        print(f"elapsed {time.time()-t0:.0f}s", flush=True)
    src = BEST if BEST.is_file() else LAST
    if not src.is_file():
        raise SystemExit("no weights")
    shutil.copy2(src, DEST)
    print(f"copied {src} -> {DEST}", flush=True)
    dump_metrics()

if __name__ == "__main__":
    main()
