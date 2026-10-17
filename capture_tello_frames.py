#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tello 建模用：从 Kinect 运行时目录采集彩色帧（可选 depth_meta）。

用法（Windows）:
  cd /d E:\\全家智能安全调度系统
  python capture_tello_frames.py --once
  python capture_tello_frames.py --interval 0.8
  python capture_tello_frames.py --interval 1 --no-meta

控制（连续模式）:
  Ctrl+C  停止
  （可选）若安装了 msvcrt：按 S 立即再存一张，按 Q 退出

前提:
  Kinect 直连 / keeper 已在写 tower_ui\\kinect_runtime\\color.jpg
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "tower_ui" / "kinect_runtime"
OUT_COLOR = ROOT / "datasets" / "tello_kinect" / "raw" / "color"
OUT_META = ROOT / "datasets" / "tello_kinect" / "raw" / "depth_meta"
COLOR_SRC = RUNTIME / "color.jpg"
META_SRC = RUNTIME / "depth_meta.json"


def ts_name() -> str:
    # 精确到毫秒，避免同秒覆盖
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def capture_one(*, copy_meta: bool) -> tuple[Path, Path | None]:
    if not COLOR_SRC.is_file():
        raise FileNotFoundError(
            f"找不到彩色帧: {COLOR_SRC}\n请先启动 Kinect（kinect_direct / keeper）并确认出图。"
        )
    OUT_COLOR.mkdir(parents=True, exist_ok=True)
    OUT_META.mkdir(parents=True, exist_ok=True)

    name = ts_name()
    dest_color = OUT_COLOR / f"{name}.jpg"
    # 复制时源文件可能正被写入：直接 copy，失败则短重试
    last_err = None
    for _ in range(5):
        try:
            shutil.copy2(COLOR_SRC, dest_color)
            last_err = None
            break
        except OSError as e:
            last_err = e
            time.sleep(0.05)
    if last_err is not None:
        raise last_err

    dest_meta = None
    if copy_meta and META_SRC.is_file():
        dest_meta = OUT_META / f"{name}.json"
        try:
            shutil.copy2(META_SRC, dest_meta)
        except OSError as e:
            print(f"[warn] depth_meta 复制失败: {e}", flush=True)
            dest_meta = None
    return dest_color, dest_meta


def try_key() -> str | None:
    """Windows 非阻塞读键；不可用则返回 None。"""
    try:
        import msvcrt
    except ImportError:
        return None
    if msvcrt.kbhit():
        ch = msvcrt.getwch()
        return ch.lower() if isinstance(ch, str) else None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="从 Kinect runtime 采集 Tello 训练彩图")
    ap.add_argument("--interval", type=float, default=1.0, help="连续采集间隔秒（默认 1.0）")
    ap.add_argument("--once", action="store_true", help="只保存一张后退出")
    ap.add_argument("--no-meta", action="store_true", help="不复制 depth_meta.json")
    ap.add_argument("--runtime", type=str, default="", help="覆盖 Kinect runtime 目录")
    args = ap.parse_args()

    global RUNTIME, COLOR_SRC, META_SRC
    if args.runtime:
        RUNTIME = Path(args.runtime)
        COLOR_SRC = RUNTIME / "color.jpg"
        META_SRC = RUNTIME / "depth_meta.json"

    copy_meta = not args.no_meta
    print(f"源: {COLOR_SRC}", flush=True)
    print(f"出: {OUT_COLOR}", flush=True)
    print(f"meta: {'是' if copy_meta else '否'} -> {OUT_META}", flush=True)

    if args.once:
        c, m = capture_one(copy_meta=copy_meta)
        print(f"[ok] color -> {c} ({c.stat().st_size} bytes)", flush=True)
        if m:
            print(f"[ok] meta  -> {m}", flush=True)
        else:
            print("[ok] meta  -> (跳过或源不存在)", flush=True)
        return 0

    print(f"连续采集 interval={args.interval}s  Ctrl+C 停止；S=加拍 Q=退出", flush=True)
    n = 0
    try:
        while True:
            c, m = capture_one(copy_meta=copy_meta)
            n += 1
            extra = f" meta={m.name}" if m else ""
            print(f"[{n}] {c.name} ({c.stat().st_size}B){extra}", flush=True)

            # 间隔内轮询按键
            t_end = time.time() + max(0.05, args.interval)
            while time.time() < t_end:
                key = try_key()
                if key == "q":
                    print("退出", flush=True)
                    return 0
                if key == "s":
                    c2, m2 = capture_one(copy_meta=copy_meta)
                    n += 1
                    print(f"[{n}] (S) {c2.name}", flush=True)
                time.sleep(0.05)
    except KeyboardInterrupt:
        print(f"\n已停止，共 {n} 张", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())