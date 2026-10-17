#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tello 弱监督预标注 + 小模型训练流水线。"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import torch
from ultralytics import YOLO

ROOT = Path(r"E:\全家智能安全调度系统")
RAW = ROOT / "datasets" / "tello_kinect" / "raw" / "color"
DS = ROOT / "datasets" / "tello_kinect"
IMG_TRAIN = DS / "images" / "train"
IMG_VAL = DS / "images" / "val"
LBL_TRAIN = DS / "labels" / "train"
LBL_VAL = DS / "labels" / "val"
WEIGHTS_DIR = DS / "weights"
EXPORTS = DS / "exports"
WORLD_WT = ROOT / "yolov8s-worldv2.pt"
REPORT = DS / "auto_label_train_report.json"

WORLD_CLASSES = [
    "person", "chair", "keyboard", "laptop", "tv", "cell phone",
    "bottle", "cup", "book", "backpack", "mouse", "remote",
    "toy drone", "drone", "tello", "DJI Tello", "white quadcopter",
    "small drone", "mini drone", "quadcopter", "white toy drone",
    "white drone", "small white drone", "handheld drone",
]
DRONE_ALIAS = {
    "toy drone": "tello", "drone": "tello", "tello": "tello",
    "dji tello": "tello", "DJI Tello": "tello",
    "white quadcopter": "tello", "small drone": "tello",
    "mini drone": "tello", "quadcopter": "tello",
    "quadcopter drone": "tello", "white toy drone": "tello",
    "uav": "tello", "white drone": "tello",
    "small white drone": "tello", "handheld drone": "tello",
}
PROMOTE = {"bottle", "cell phone", "cup", "mouse", "remote"}
CONF_DRONE = 0.05
CONF_PROMOTE = 0.12
MAX_ASPECT = 5.0
# 像素框相对图像面积
AREA_MIN, AREA_MAX = 0.0015, 0.20


def file_md5(p: Path, chunk=1 << 20) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def clear_dir(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    for p in d.glob("*"):
        if p.is_file():
            p.unlink()


def prepare_split(seed=42):
    files = sorted(RAW.glob("*.jpg"), key=lambda p: p.name)
    print(f"raw jpg: {len(files)}")
    # 去重：相同 md5 只留一张
    uniq, seen = [], set()
    for p in files:
        dig = file_md5(p)
        if dig in seen:
            continue
        seen.add(dig)
        uniq.append(p)
    print(f"after md5 dedupe: {len(uniq)}")

    # 按文件名时间排序后 80/20（可复现）；打乱前先排序
    # time-stratified: sort by filename timestamp, take every 5th for val (~20%)
    indexed = sorted(uniq, key=lambda x: x.name)
    val_list = [p for i, p in enumerate(indexed) if i % 5 == 0]
    train_set = [p for i, p in enumerate(indexed) if i % 5 != 0]
    if not val_list:
        n_val = max(1, int(round(len(indexed) * 0.2)))
        val_list = indexed[:n_val]
        train_set = indexed[n_val:]

    for d in (IMG_TRAIN, IMG_VAL, LBL_TRAIN, LBL_VAL):
        clear_dir(d)

    def copy_list(lst, dest):
        for p in lst:
            shutil.copy2(p, dest / p.name)

    copy_list(train_set, IMG_TRAIN)
    copy_list(val_list, IMG_VAL)
    split_info = {
        "method": "md5_dedupe + time_stratified_every5th_val + ~80/20",
        "seed": seed,
        "raw": len(files),
        "unique": len(uniq),
        "train": len(train_set),
        "val": len(val_list),
        "train_names": [p.name for p in train_set],
        "val_names": [p.name for p in val_list],
    }
    (DS / "split_info.json").write_text(
        json.dumps(split_info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"split train={len(train_set)} val={len(val_list)}")
    return train_set, val_list


def box_ok(xyxy, w, h):
    x1, y1, x2, y2 = xyxy
    bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
    area = (bw * bh) / (w * h)
    aspect = max(bw, bh) / min(bw, bh)
    if area < AREA_MIN or area > AREA_MAX:
        return False
    if aspect > MAX_ASPECT:
        return False
    return True


def to_yolo_line(xyxy, w, h, cls=0):
    x1, y1, x2, y2 = xyxy
    cx = ((x1 + x2) / 2.0) / w
    cy = ((y1 + y2) / 2.0) / h
    bw = (x2 - x1) / w
    bh = (y2 - y1) / h
    # clamp
    cx, cy = min(1, max(0, cx)), min(1, max(0, cy))
    bw, bh = min(1, max(1e-6, bw)), min(1, max(1e-6, bh))
    return f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def auto_label_image(model, device, img_path: Path, lbl_path: Path):
    img = cv2.imread(str(img_path))
    if img is None:
        lbl_path.write_text("", encoding="utf-8")
        return 0, "read_fail"
    h, w = img.shape[:2]
    with torch.no_grad():
        results = model(img, verbose=False, imgsz=640, device=device, conf=0.05)
    cands = []
    for r in results:
        names = r.names
        for b in r.boxes:
            raw = names.get(int(b.cls[0]), "?")
            conf = float(b.conf[0])
            xyxy = [float(v) for v in b.xyxy[0]]
            label = DRONE_ALIAS.get(raw, DRONE_ALIAS.get(raw.lower(), raw))
            is_drone = label == "tello" or raw.lower() in DRONE_ALIAS
            if is_drone:
                if conf < CONF_DRONE:
                    continue
                kind = "drone"
            elif raw in PROMOTE or raw.lower() in PROMOTE:
                if conf < CONF_PROMOTE:
                    continue
                kind = "promote"
            else:
                continue
            if not box_ok(xyxy, w, h):
                continue
            cands.append((conf, kind, xyxy, raw))

    if not cands:
        # 二次：仅 drone 提示
        only = [
            "toy drone", "drone", "tello", "DJI Tello", "white quadcopter",
            "small drone", "mini drone", "quadcopter", "white toy drone",
            "white drone", "handheld drone",
        ]
        try:
            model.set_classes(only)
            with torch.no_grad():
                results = model(img, verbose=False, imgsz=640, device=device, conf=0.05)
            for r in results:
                for b in r.boxes:
                    conf = float(b.conf[0])
                    xyxy = [float(v) for v in b.xyxy[0]]
                    if conf < CONF_DRONE or not box_ok(xyxy, w, h):
                        continue
                    cands.append((conf, "second", xyxy, r.names.get(int(b.cls[0]), "?")))
        finally:
            model.set_classes(WORLD_CLASSES)

    if not cands:
        lbl_path.write_text("", encoding="utf-8")
        return 0, "neg"

    # 取最高 conf 一个主框（单机场景）；若多个高分且中心远，可保留最多 2 个
    cands.sort(key=lambda x: -x[0])
    keep = [cands[0]]
    if len(cands) > 1:
        c0 = ((cands[0][2][0] + cands[0][2][2]) / 2, (cands[0][2][1] + cands[0][2][3]) / 2)
        for c in cands[1:3]:
            cx = ((c[2][0] + c[2][2]) / 2, (c[2][1] + c[2][3]) / 2)
            dist = ((cx[0] - c0[0]) ** 2 + (cx[1] - c0[1]) ** 2) ** 0.5
            if dist > 80 and c[0] >= 0.15:
                keep.append(c)
                break

    lines = [to_yolo_line(xyxy, w, h, 0) for _, _, xyxy, _ in keep]
    lbl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(keep), keep[0][1]


def auto_label_all():
    assert WORLD_WT.is_file(), f"missing {WORLD_WT}"
    model = YOLO(str(WORLD_WT))
    model.set_classes(WORLD_CLASSES)
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"auto-label device={device}")

    stats = {"pos": 0, "neg": 0, "boxes": 0, "by_kind": {}}
    for split, img_dir, lbl_dir in (
        ("train", IMG_TRAIN, LBL_TRAIN),
        ("val", IMG_VAL, LBL_VAL),
    ):
        imgs = sorted(img_dir.glob("*.jpg"))
        for i, ip in enumerate(imgs, 1):
            lp = lbl_dir / (ip.stem + ".txt")
            nbox, kind = auto_label_image(model, device, ip, lp)
            if nbox == 0:
                stats["neg"] += 1
            else:
                stats["pos"] += 1
                stats["boxes"] += nbox
                stats["by_kind"][kind] = stats["by_kind"].get(kind, 0) + 1
            if i % 20 == 0 or i == len(imgs):
                print(f"  [{split}] {i}/{len(imgs)} pos={stats['pos']} neg={stats['neg']}", flush=True)
    return stats


def write_yaml():
    # ultralytics on Windows prefers forward slashes or escaped
    path = str(DS).replace("\\", "/")
    yml = f"""# Auto-generated for Tello weak-label train
path: {path}
train: images/train
val: images/val
names:
  0: tello
"""
    yp = DS / "tello.yaml"
    yp.write_text(yml, encoding="utf-8")
    print(f"wrote {yp}")
    return yp


def train(epochs=60):
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(ROOT / "yolov8n.pt"))
    device = 0 if torch.cuda.is_available() else "cpu"
    print(f"train device={device} epochs={epochs}")
    results = model.train(
        data=str(DS / "tello.yaml"),
        epochs=epochs,
        imgsz=640,
        batch=8,
        device=device,
        project=str(EXPORTS),
        name="tello_n_auto",
        exist_ok=True,
        patience=20,
        verbose=True,
    )
    # copy best
    run_dir = EXPORTS / "tello_n_auto"
    best = run_dir / "weights" / "best.pt"
    last = run_dir / "weights" / "last.pt"
    dest = WEIGHTS_DIR / "tello_yolov8n.pt"
    if best.is_file():
        shutil.copy2(best, dest)
        print(f"copied best -> {dest}")
    elif last.is_file():
        shutil.copy2(last, dest)
        print(f"copied last -> {dest}")
    else:
        print("WARN: no weights found")
        dest = None
    return dest, run_dir, results


def main():
    t0 = time.time()
    prepare_split(seed=42)
    stats = auto_label_all()
    write_yaml()
    # 正样本太少则仍训练，但报告里说明
    epochs = 60
    if stats["pos"] < 30:
        epochs = 40
        print(f"few positives ({stats['pos']}), epochs={epochs}")
    dest, run_dir, results = train(epochs=epochs)

    # metrics from results if available
    metrics = {}
    try:
        # ultralytics Result object
        if hasattr(results, "results_dict"):
            metrics = dict(results.results_dict)
        elif hasattr(results, "box"):
            metrics = {"note": "see runs dir"}
    except Exception as e:
        metrics = {"error": str(e)}

    # read results.csv if exists
    csv_path = run_dir / "results.csv"
    last_row = None
    if csv_path.is_file():
        lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) >= 2:
            headers = [h.strip() for h in lines[0].split(",")]
            vals = [v.strip() for v in lines[-1].split(",")]
            last_row = dict(zip(headers, vals))

    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": round(time.time() - t0, 1),
        "label_stats": stats,
        "weights": str(dest) if dest else None,
        "run_dir": str(run_dir),
        "epochs": epochs,
        "metrics_last_epoch": last_row,
        "note": "弱监督预标注（YOLO-World + promote），需人工抽查标签质量",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("DONE")


if __name__ == "__main__":
    main()