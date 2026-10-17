# -*- coding: utf-8 -*-
"""检查悬停控制器要用的 UDP 位姿通道（8899）能否收到 kinect_yolo3d 的推送。

对照：塔台用的 8898 是通的（/api/status 的 vision.pose 有值且 age 很小），
所以如果这里收不到，问题就出在第二路推送或本机绑定上。
"""
import socket
import time

PORT = 8899
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.ioctl(socket.SIO_UDP_CONNRESET, False)
except Exception:
    pass
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("", PORT))
except OSError as e:
    print(f"bind {PORT} 失败（可能已被占用）：{e}")
    raise SystemExit(1)
s.settimeout(5)
print(f"监听 0.0.0.0:{PORT} …")

got = 0
t0 = time.time()
while time.time() - t0 < 8:
    try:
        d, a = s.recvfrom(2048)
    except socket.timeout:
        print("  （5 秒超时，无包）")
        break
    got += 1
    print(f"  <- from {a}  {d[:90]!r}")
print(f"共收到 {got} 个包")
