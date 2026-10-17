# -*- coding: utf-8 -*-
"""Tello 链路诊断（不起飞，只做通信层检查）

关键认知：Tello 固件**不响应 ICMP ping** —— ping 不通不代表离线。
唯一可靠的判据是 UDP 192.168.10.1:8889 的 command 握手有没有 "ok" 回执。
"""
import re
import socket
import subprocess
import time


def sh(args, timeout=15):
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
        raw = (p.stdout or b"") + (p.stderr or b"")
    except Exception:
        return ""
    for enc in ("gbk", "utf-8", "mbcs"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "ignore")


print("=" * 64)
print("1) 无线网卡当前关联")
print("=" * 64)
out = sh(["netsh", "wlan", "show", "interfaces"])
for key in ("SSID", "状态", "State", "信号", "Signal", "BSSID", "描述", "Description"):
    for line in out.splitlines():
        m = re.match(rf"^\s*{key}\s*:\s*(.+?)\s*$", line)
        if m:
            print(f"   {key:<10}: {m.group(1)}")
            break

print()
print("=" * 64)
print("2) ipconfig 里 WLAN 的地址")
print("=" * 64)
ipcfg = sh(["ipconfig"])
cur = None
for line in ipcfg.splitlines():
    if re.match(r"^\S", line) and "适配器" in line or (line and not line.startswith(" ")):
        cur = line.strip()
    m = re.search(r"IPv4[^\d:]{0,30}:\s*(\d+\.\d+\.\d+\.\d+)", line)
    if m and cur and ("WLAN" in cur or "无线" in cur or "wireless" in cur.lower()):
        print(f"   {m.group(1)}   ({cur})")

print()
print("=" * 64)
print("3) UDP 握手测试（真正的在线判据）")
print("=" * 64)
TELLO = ("192.168.10.1", 8889)


def udp_cmd(cmd, timeout=6.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.bind(("", 0))
        s.sendto(cmd.encode(), TELLO)
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                data, addr = s.recvfrom(1024)
            except socket.timeout:
                return None
            text = data.decode("ascii", "ignore").strip()
            if text:
                return text
        return None
    except Exception as e:
        return f"ERR {type(e).__name__}: {e}"
    finally:
        s.close()


for c in ("command", "battery?", "sdk?", "sn?"):
    r = udp_cmd(c)
    tag = "OK" if (r and not r.startswith("ERR")) else ("--" if r is None else "!!")
    print(f"   [{tag}] {c:<10} -> {r}")

print()
print("=" * 64)
print("4) 视频端口 11111 是否有数据（不起飞也能 streamon）")
print("=" * 64)
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.5)
    s.bind(("", 11111))
    s.close()
    print("   11111 空闲 -> 当前没有视频流（正常，需要先 streamon）")
except OSError:
    print("   11111 被占用 -> ffmpeg 可能正在收流")

r = udp_cmd("streamon", timeout=8.0)
print(f"   streamon -> {r}")
if r is None or str(r).startswith("ERR"):
    print("   （注意：streamon 常常不回 ok，但视频包可能已经在发 —— 以数据包为准）")

flow = 0
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(5.0)
try:
    s.bind(("", 11111))
    t0 = time.time()
    while time.time() - t0 < 5:
        try:
            d, _ = s.recvfrom(2048)
            flow += len(d)
        except socket.timeout:
            break
except OSError as e:
    flow = -1
    print(f"   端口占用: {e}")
finally:
    s.close()
if flow > 0:
    print(f"   收到视频数据 {flow:,} 字节 -> 视频链路可用！")
else:
    print("   未收到视频数据 -> Tello 可能未开机 / 命令通道冻结")
