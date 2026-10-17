#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kinect v2 直连采集器 —— 取代 C# kinect_bridge.exe 这一层。

为什么要去掉 bridge：
  原来的链路是 Kinect → C# bridge → 写 jpg/bin → Python 读文件。
  中间那一层每帧用 Windows 的 File.Replace 做原子替换，失败会留下
  ~RF*.TMP 垃圾，堆积后把出帧从 13fps 拖到 0.4fps，最终导致
  color.jpg 冻结 → keeper 重启 Kinect → USB 重枚举 → 鼠标键盘一起掉。
  改成 Python 直连后，帧数据留在内存里，用 os.replace 原子落盘，
  从根上不再有那一类文件替换问题。

输出规格与旧 bridge **完全一致**，因此 kinect_yolo3d.py 和塔台无需改动：
  color.jpg        960x540 JPEG
  depth_raw.bin    960x540 uint16 毫米（已配准到彩色网格）
  depth_meta.json  960x540 内参（fx=532.159 → 反算 FOV 84.1°，与彩色一致）
  status.json      设备状态
  motion.json      画面变化度

用法：
    python kinect_direct.py              # 常驻采集
    python kinect_direct.py --probe      # 只取一帧并打印，然后退出
"""

import argparse
import ctypes
import json
import os
import sys
import time

import cv2
import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

import pykinect2_compat

pykinect2_compat.ensure_patched()

from pykinect2 import PyKinectRuntime, PyKinectV2

ROOT = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.environ.get(
    "KINECT_DIR", os.path.join(ROOT, "tower_ui", "kinect_runtime"))
os.makedirs(RUNTIME, exist_ok=True)

COLOR_PATH = os.path.join(RUNTIME, "color.jpg")
DEPTH_PATH = os.path.join(RUNTIME, "depth_raw.bin")
META_PATH = os.path.join(RUNTIME, "depth_meta.json")
STATE_PATH = os.path.join(RUNTIME, "status.json")
MOTION_PATH = os.path.join(RUNTIME, "motion.json")

# 输出网格（与旧 bridge 一致）：彩色 1920x1080 的 1/2
OUT_W, OUT_H = 960, 540
# 深度原始网格
DW, DH = 512, 424
# 输出网格的等效内参：由 1920x1080 的 fx≈1064 折半而来
# 反算水平 FOV = 2*atan(480/532.159) = 84.1°，与 Kinect v2 彩色官方值吻合
FX, FY, CX, CY = 532.159, 532.199, 479.5, 269.5

JPEG_QUALITY = 78
MOTION_INTERVAL = 0.15      # 变化度计算节奏（秒）


def atomic_write(path, data: bytes, retries=8, delay=0.01):
    """原子落盘：写临时文件 + os.replace，带重试。

    为什么必须用 os.replace 而不是 Windows 的 File.Replace API：
    File.Replace 失败时会在目录里留下 ~RF*.TMP 残留，越堆越多会把
    出帧从 13fps 拖到 0.4fps（旧 bridge 的故障根源）。

    为什么必须重试：Windows 上只要别的进程正打开着目标文件
    （识别器 np.fromfile、塔台给浏览器供 /kinect/color.jpg 都会打开它），
    os.replace 就会 PermissionError(WinError 5)。Python 打开文件时不带
    FILE_SHARE_DELETE，所以这里的对策是短间隔重试 —— 读端持有句柄
    通常只有几毫秒，重试基本都能成功。
    """
    tmp = path + ".tmp"
    for i in range(retries):
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            return True
        except (PermissionError, OSError):
            if i == retries - 1:
                return False
            time.sleep(delay)
    return False


def atomic_write_text(path, text: str):
    atomic_write(path, text.encode("utf-8"))


def write_status(connected, frames_active, note):
    state = {
        "connected": connected,
        "available": connected,
        "frames_active": frames_active,
        "sensor": "Kinect v2",
        "note": note,
        "body_count": 0,
        "bodies": [],
        "color_width": 1920,
        "color_height": 1080,
        "depth_width": OUT_W,
        "depth_height": OUT_H,
        "color_url": "/kinect/color.jpg",
        "depth_url": "/kinect/depth.jpg",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        atomic_write_text(STATE_PATH, json.dumps(state, ensure_ascii=False))
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="取一帧并打印后退出")
    ap.add_argument("--fps", type=float, default=25.0, help="采集帧率上限")
    args = ap.parse_args()

    print(f"Kinect 直连采集器  输出目录: {RUNTIME}")
    print(f"彩色 {OUT_W}x{OUT_H} · 深度配准到彩色网格 · 内参 fx={FX}")

    try:
        kinect = PyKinectRuntime.PyKinectRuntime(
            PyKinectV2.FrameSourceTypes_Color | PyKinectV2.FrameSourceTypes_Depth)
    except Exception as e:
        print(f"打开 Kinect 失败: {type(e).__name__}: {e}")
        print("确认：Kinect 已接 USB3.0 且外接电源、KinectService 在运行")
        return 1

    n = DW * DH
    csp_buf = (PyKinectV2._ColorSpacePoint * n)()

    atomic_write_text(META_PATH, json.dumps({
        "width": OUT_W, "height": OUT_H, "unit": "mm",
        "source": "color_registered", "fx": FX, "fy": FY, "cx": CX, "cy": CY,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False))

    prev_depth = None
    last_motion = 0.0
    frame_count = 0
    t_start = time.time()
    interval = 1.0 / max(1.0, args.fps)

    print("开始采集（Ctrl+C 停止）")
    try:
        while True:
            t0 = time.time()

            if kinect.has_new_color_frame():
                color = kinect.get_last_color_frame()      # BGRA 1920x1080x4
                if color is not None and color.size == 1920 * 1080 * 4:
                    img = color.reshape((1080, 1920, 4))[:, :, :3]  # → BGR
                    small = cv2.resize(img, (OUT_W, OUT_H),
                                       interpolation=cv2.INTER_AREA)
                    ok, buf = cv2.imencode(
                        ".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                    if ok:
                        atomic_write(COLOR_PATH, buf.tobytes())
                    frame_count += 1

            if kinect.has_new_depth_frame():
                depth = kinect.get_last_depth_frame()      # uint16 512x424
                if depth is not None and depth.size == n:
                    ptr = depth.ctypes.data_as(ctypes.POINTER(ctypes.c_ushort))
                    try:
                        kinect._mapper.MapDepthFrameToColorSpace(
                            ctypes.c_uint(n), ptr, ctypes.c_uint(n), csp_buf)
                    except Exception as e:
                        print(f"[配准] 失败: {type(e).__name__}: {e}")
                    else:
                        arr = np.frombuffer(
                            csp_buf,
                            dtype=np.dtype([("x", np.float32),
                                            ("y", np.float32)]))
                        # 1920x1080 彩色空间坐标 → 960x540 输出网格（直接折半，
                        # 比先铺满 1920x1080 再缩放的密度高 4 倍）
                        xs = arr["x"] * 0.5
                        ys = arr["y"] * 0.5
                        ok_mask = (np.isfinite(xs) & np.isfinite(ys)
                                   & (xs >= 0) & (xs < OUT_W)
                                   & (ys >= 0) & (ys < OUT_H)
                                   & (depth > 0))
                        reg = np.zeros((OUT_H, OUT_W), np.uint16)
                        reg[ys[ok_mask].astype(np.int32),
                            xs[ok_mask].astype(np.int32)] = depth[ok_mask]
                        try:
                            atomic_write(DEPTH_PATH, reg.astype("<u2").tobytes())
                        except OSError:
                            pass

                        # 变化度：与上一帧有效深度的差异
                        now = time.time()
                        if now - last_motion >= MOTION_INTERVAL:
                            last_motion = now
                            if prev_depth is not None:
                                m = (prev_depth > 0) & (reg > 0)
                                cnt = int(m.sum())
                                if cnt > 1000:
                                    diff = np.abs(
                                        reg[m].astype(np.int32)
                                        - prev_depth[m].astype(np.int32))
                                    mean_mm = float(diff.mean())
                                    ratio = float((diff > 30).sum()) / cnt
                                    motion = {
                                        "changed": bool(ratio > 0.01),
                                        "ratio": round(ratio, 4),
                                        "mean_mm": round(mean_mm, 2),
                                    }
                                    try:
                                        atomic_write_text(
                                            MOTION_PATH,
                                            json.dumps(motion, ensure_ascii=False))
                                    except OSError:
                                        pass
                            prev_depth = reg.copy()

            # 注意要 frame_count > 0：否则启动阶段每帧都满足 %20==0，日志被刷爆
            if frame_count > 0 and frame_count % 20 == 0:
                write_status(True, True, "Color + registered depth (Python direct)")
                if frame_count % 100 == 0:
                    fps = frame_count / max(1e-6, time.time() - t_start)
                    print(f"{time.strftime('%H:%M:%S')} 已出帧 {frame_count} "
                          f"（{fps:.1f} fps）")

            if args.probe and frame_count >= 1:
                print("probe 完成")
                print(f"  color.jpg  {os.path.getsize(COLOR_PATH):,} B")
                print(f"  depth_raw  {os.path.getsize(DEPTH_PATH):,} B")
                d = np.fromfile(DEPTH_PATH, dtype="<u2").reshape((OUT_H, OUT_W))
                v = d[d > 0]
                print(f"  深度非零 {v.size} 像素，范围 {int(v.min())}~{int(v.max())} mm，"
                      f"中位数 {int(np.median(v))} mm")
                break

            dt = interval - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)

    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        try:
            kinect.close()
        except Exception:
            pass
        write_status(False, False, "stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
