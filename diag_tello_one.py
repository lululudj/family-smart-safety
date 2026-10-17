# -*- coding: utf-8 -*-
"""Tello 命令通道纯握手测试（多轮）。

用法: python diag_tello_one.py [轮数]
每轮新建 socket -> command -> battery?，观察是否有 ok / 电量。
"""
import socket
import sys
import time

TELLO = ("192.168.10.1", 8889)


def mk():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.ioctl(socket.SIO_UDP_CONNRESET, False)
    except Exception:
        pass
    s.bind(("", 0))
    s.settimeout(4.0)
    return s


def cmd(s, c, w=4.0):
    try:
        s.sendto(c.encode(), TELLO)
    except Exception as e:
        return f"send-ERR {type(e).__name__}"
    t0 = time.time()
    while time.time() - t0 < w:
        try:
            d, _ = s.recvfrom(1024)
        except socket.timeout:
            return None
        except OSError:
            return None
        t = d.decode("ascii", "ignore").strip()
        if t:
            return t
    return None


rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
for r in range(rounds):
    s = mk()
    print(f"轮{r}: command -> {cmd(s, 'command')!r}   battery? -> {cmd(s, 'battery?')!r}")
    s.close()
    time.sleep(1.0)
print("完成")
