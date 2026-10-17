# -*- coding: utf-8 -*-
"""验证 pykinect2 能否直接接管 Kinect v2（不经过 C# bridge）"""
import sys

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

try:
    from pykinect2 import PyKinectRuntime, PyKinectV2
except Exception as e:
    print("导入失败:", e)
    sys.exit(1)

print("pykinect2 导入成功")
print("FrameSourceTypes:", PyKinectV2.FrameSourceTypes_Color,
      PyKinectV2.FrameSourceTypes_Depth)

try:
    k = PyKinectRuntime.PyKinectRuntime(
        PyKinectV2.FrameSourceTypes_Color | PyKinectV2.FrameSourceTypes_Depth)
except Exception as e:
    print("打开设备失败:", type(e).__name__, e)
    sys.exit(2)

print("设备已打开")

import time
for i in range(10):
    time.sleep(0.3)
    if not k.has_new_color_frame():
        continue
    color = k.get_last_color_frame()      # BGRA 1920x1080x4
    depth = k.get_last_depth_frame()      # uint16 512x424
    print(f"  帧{i}: color 元素数 {color.size} (1920*1080*4={1920*1080*4}), "
          f"depth {depth.size} (512*424={512*424})")
    if color.size == 1920 * 1080 * 4:
        img = color.reshape((1080, 1920, 4))
        print(f"    彩色 reshape OK {img.shape}, 中心像素 BGR={img[540,960,:3]}")
    if depth.size == 512 * 424:
        d = depth.reshape((424, 512))
        valid = d[d > 0]
        print(f"    深度 reshape OK {d.shape}, 有效像素 {valid.size}, "
              f"中位数 {int(np.median(valid)) if valid.size else 0} mm")
    k._clock.stop() if hasattr(k, "_clock") else None
    break

k.close()
print("测试结束")
