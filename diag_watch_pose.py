# -*- coding: utf-8 -*-
"""连续监听 8899 位姿（kinect_yolo3d 直推），用来定位"多高开始看不见 Tello"。

比读塔台 /api/status 更准：能逐帧看到"本帧有目标 / 无目标"，也不产生 HTTP 连接。
用法: python diag_watch_pose.py [秒数]
"""
import json
import socket
import sys
import time

DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 35.0
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.ioctl(socket.SIO_UDP_CONNRESET, False)
except Exception:
    pass
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("", 8899))
s.settimeout(1.0)

print(f"监听 8899 共 {DUR:.0f}s —— 请把 Tello 依次停在 30cm / 60cm / 100cm 高度各 5 秒")
t0 = time.time()
hit = miss = 0
last_ok = True
while time.time() - t0 < DUR:
    try:
        d, _ = s.recvfrom(2048)
    except socket.timeout:
        continue
    try:
        p = json.loads(d.decode("utf-8", "ignore"))
    except ValueError:
        continue
    if p.get("x") is None:
        miss += 1
        if last_ok:
            print(f"{time.strftime('%H:%M:%S')}  --- 目标丢失 ---")
            last_ok = False
    else:
        hit += 1
        last_ok = True
        print(f"{time.strftime('%H:%M:%S')}  x={p['x']:+.3f} y={p['y']:+.3f} "
              f"z={p['z']:6.3f} conf={p.get('conf') or 0:.3f}")
print(f"结束：命中 {hit} · 丢失 {miss}")
