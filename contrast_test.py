# -*- coding: utf-8 -*-
"""对照实验：临时停掉 yolo（color.jpg 的主要读者），观察 bridge 写文件是否恢复。

目的：区分是"bridge 自己写不动"（GDI+/NTFS 问题）还是"读者竞争抢 file handle"。
keeper 每 15 秒巡检，约 12 秒的窗口后可以自己恢复 —— 正好够观察。
"""
import os
import time

import psutil

RT = os.path.join("tower_ui", "kinect_runtime")
COLOR = os.path.join(RT, "color.jpg")
ERR = os.path.join("tower_ui", "bridge_err.log")


def nlines(p):
    try:
        with open(p, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def find_yolo():
    hits = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
            if "kinect_yolo3d" in cl and p.info["name"] and p.info["name"].startswith("py"):
                hits.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return hits


# ---------- 阶段 A：有 yolo 在跑 ----------
n0 = nlines(ERR)
stamps = set()
t0 = time.time()
while time.time() - t0 < 6:
    try:
        stamps.add(os.path.getmtime(COLOR))
    except OSError:
        pass
    time.sleep(0.02)
err_a = nlines(ERR) - n0
fps_a = len(stamps) / (time.time() - t0)
print(f"[A] 读者在线（yolo 运行中）  6s: 新帧 {len(stamps)} 个 ≈ {fps_a:.1f} fps, "
      f"写失败 {err_a} 条")

# ---------- 阶段 B：临时停掉 yolo ----------
yolos = find_yolo()
print(f"    临时停止 yolo {[p.pid for p in yolos]}...")
for p in yolos:
    try:
        p.kill()
    except Exception:
        pass
time.sleep(1.5)   # 等句柄释放

n1 = nlines(ERR)
stamps2 = set()
t1 = time.time()
while time.time() - t1 < 6:
    try:
        stamps2.add(os.path.getmtime(COLOR))
    except OSError:
        pass
    time.sleep(0.02)
err_b = nlines(ERR) - n1
fps_b = len(stamps2) / (time.time() - t1)
print(f"[B] 读者停止（无 yolo）      6s: 新帧 {len(stamps2)} 个 ≈ {fps_b:.1f} fps, "
      f"写失败 {err_b} 条")

print()
print("=" * 62)
print("对照结论")
print("=" * 62)
print(f"  帧率     {fps_a:.1f} fps  ->  {fps_b:.1f} fps")
print(f"  写失败   {err_a} 条      ->  {err_b} 条")
if err_b < err_a * 0.3 and err_a > 0:
    print("  → 读者竞争是主因：有人在读 color.jpg 时，bridge 的 File.Replace 排不上队")
    print("    （治理：降读取频率 / 别用 ReplaceFile / 让 Kinect 独占主机）")
elif fps_b > fps_a * 1.5:
    print("  → 停掉读者后帧率明显回升，说明读端把写端饿住了")
else:
    print("  → 停掉读者也没好转，问题在 bridge 自身（GDI+ Save / NTFS Replace）")

alive = find_yolo()
print(f"\n  yolo 当前状态: {'已由 keeper 自动拉起' if alive else '未运行（keeper 稍后会拉起）'}")
