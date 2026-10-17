# -*- coding: utf-8 -*-
"""读 8890 state 广播里的温度/电量 —— 判断 Tello 是否过热。"""
import socket
import time

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("", 8890))
s.settimeout(2.0)
last = None
n = 0
t0 = time.time()
while time.time() - t0 < 6:
    try:
        d, _ = s.recvfrom(1024)
        last = d.decode("ascii", "ignore").strip()
        n += 1
    except socket.timeout:
        pass
print(f"收到 {n} 个 state 包")
if last:
    f = dict(x.split(":", 1) for x in last.split(";") if ":" in x)
    print(f"  bat={f.get('bat')}%  templ={f.get('templ')}C  temph={f.get('temph')}C  "
          f"h={f.get('h')}  tof={f.get('tof')}  baro={f.get('baro')}")
else:
    print("  没有 state 广播 -> Tello 应用层可能已停")
