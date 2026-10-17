# -*- coding: utf-8 -*-
"""测试能否用 pykinect2 的 CoordinateMapper 把深度配准到彩色网格。

下游（kinect_yolo3d）依赖一张"与彩色像素对齐"的深度图：
深度视场 70.6 度、彩色 84.1 度，两者不同，必须靠 SDK 的配准，
不能简单按比例缩放（那样 3D 定位会错位）。
"""
import ctypes
import sys
import time

import numpy as np

import pykinect2_compat
pykinect2_compat.ensure_patched()

from pykinect2 import PyKinectRuntime, PyKinectV2

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

k = PyKinectRuntime.PyKinectRuntime(
    PyKinectV2.FrameSourceTypes_Color | PyKinectV2.FrameSourceTypes_Depth)
print("设备已打开, mapper =", k._mapper)

DW, DH = 512, 424
CW, CH = 1920, 1080

# 等一帧
for _ in range(40):
    time.sleep(0.1)
    if k.has_new_depth_frame():
        break

depth = k.get_last_depth_frame()
print("深度帧:", depth.shape, depth.dtype)

try:
    CSP = PyKinectV2._ColorSpacePoint
    print("_ColorSpacePoint 可用, sizeof =", ctypes.sizeof(CSP))
except Exception as e:
    print("_ColorSpacePoint 不可用:", e)
    CSP = None

if CSP is not None:
    n = DW * DH
    buf = (CSP * n)()
    depth_ptr = depth.ctypes.data_as(ctypes.POINTER(ctypes.c_ushort))
    t0 = time.time()
    try:
        k._mapper.MapDepthFrameToColorSpace(
            ctypes.c_uint(n), depth_ptr, ctypes.c_uint(n), buf)
    except Exception as e:
        print("调用失败:", type(e).__name__, e)
        buf = None
    dt = (time.time() - t0) * 1000
    if buf is not None:
        print(f"MapDepthFrameToColorSpace 成功，耗时 {dt:.0f} ms")

        # 转成 numpy
        arr = np.frombuffer(buf, dtype=np.dtype(
            [("x", np.float32), ("y", np.float32)])).copy()
        xs = arr["x"]
        ys = arr["y"]
        valid = np.isfinite(xs) & np.isfinite(ys)
        valid &= (xs >= 0) & (xs < CW) & (ys >= 0) & (ys < CH)
        valid &= (depth > 0)
        print(f"有效映射点 {int(valid.sum())} / {n}")

        depth_map = np.zeros((CH, CW), np.uint16)
        depth_map[ys[valid].astype(int), xs[valid].astype(int)] = depth[valid]
        print("配准深度图形状:", depth_map.shape,
              "非零像素:", int((depth_map > 0).sum()))

        import cv2
        small = cv2.resize(depth_map, (960, 540), interpolation=cv2.INTER_NEAREST)
        print("缩放到 960x540:", small.shape,
              "非零:", int((small > 0).sum()))
        v = small[small > 0]
        print("深度值范围 mm:", int(v.min()), "~", int(v.max()),
              "中位数", int(np.median(v)))

k.close()
print("测试结束")
