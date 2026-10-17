#!/usr/bin/env python3
"""诊断：无人机在线但视频链路开不了"""
import paramiko
import requests

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("192.168.18.181", username="orangepi", password="orangepi", timeout=10)
print("[connected]")


def run(cmd, t=60):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=t)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    print("$", cmd[:70])
    print(out or "(no out)")
    if err:
        print("[err]", err[:200])
    print("---")


run("ip -br addr show wlan0; ping -c 2 -W 2 192.168.10.1 | tail -2")

DIAG = r"""
import socket, time
def ask(cmd, timeout=8):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(timeout); s.bind(("", 0))
    t0 = time.time(); s.sendto(cmd.encode(), ("192.168.10.1", 8889))
    try:
        data, _ = s.recvfrom(2048); return "%.1fs -> %r" % (time.time()-t0, data)
    except socket.timeout: return "TIMEOUT %.1fs" % (time.time()-t0)
    finally: s.close()
print("command:", ask("command"))
print("battery?:", ask("battery?"))
print("streamon:", ask("streamon", 10))
v = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); v.settimeout(4); v.bind(("", 11111))
total = 0; t0 = time.time()
try:
    while time.time() - t0 < 4:
        data, _ = v.recvfrom(2048); total += len(data)
except socket.timeout: pass
v.close()
print("video 11111 bytes in 4s:", total)
"""
import base64
b64 = base64.b64encode(DIAG.encode()).decode()
run(f"echo {b64} | base64 -d | python3 -")

r = requests.post("http://192.168.18.181:8080/api/uav/video/start", timeout=45)
print("video/start ->", r.json())

s = requests.get("http://192.168.18.181:8080/api/status", timeout=5).json()
print("uav:", s["uav"])
print("uav_video:", s["uav_video"])

cli.close()
