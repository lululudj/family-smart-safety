# -*- coding: utf-8 -*-
"""定量监测 bridge 写文件失败率 —— 验证 Defender/索引排除 + keeper 节流的效果"""
import os
import time

RT = os.path.join("tower_ui", "kinect_runtime")
ERR = os.path.join("tower_ui", "bridge_err.log")
COLOR = os.path.join(RT, "color.jpg")


def nlines(p):
    try:
        with open(p, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


n0 = nlines(ERR)
size0 = os.path.getsize(ERR)
t0 = time.time()
stamps = []
print("监测中（60 秒）... 这期间请勿动 Kinect/USB")

last_print = t0
missed = 0
while time.time() - t0 < 60:
    time.sleep(1)
    try:
        stamps.append(os.path.getmtime(COLOR))
    except OSError:
        missed += 1      # bridge 的 File.Replace 那一瞬，目标文件会短暂不可见

n1 = nlines(ERR)
dt = time.time() - t0

# 统计 color.jpg 在这段时间内有多少个不同的更新时间点 = 实际刷新帧数
uniq = len(set(stamps))
print()
print("=" * 60)
print(f"观测窗口 {dt:.0f} 秒")
print("=" * 60)
print(f"  bridge_err.log 新增行数 : {n1 - n0} 条  (文件 {size0:,} -> {os.path.getsize(ERR):,} 字节)")
print(f"  写文件失败率          : 约 {((n1-n0)/dt):.2f} 条/秒")
print(f"  color.jpg 实际刷新次数 : {uniq} 次  → 约 {uniq/dt:.1f} fps")
print(f"  采样期间撞上替换窗口   : {missed} 次（File.Replace 那一瞬文件不可见，毫秒级，属正常现象）")
print(f"  color.jpg 当前帧龄     : {time.time() - os.path.getmtime(COLOR):.1f} 秒")
print()
if n1 - n0 == 0:
    print("  结论：写文件零失败 —— GDI+ 竞争已解除")
elif n1 - n0 < 10:
    print("  结论：偶发失败（已大幅下降），keeper 软恢复可吸收，不会重开硬件")
else:
    print("  结论：仍在高频失败 —— 需要把 Kinect 挪到专用主机（Orange Pi）")
