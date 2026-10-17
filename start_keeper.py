# -*- coding: utf-8 -*-
"""以脱离父进程的方式启动塔台守护 keeper，让它自动拉起 tower/yolo/bridge/vtuber。

keeper 是整套系统的自愈源头：只要它在，tower / yolo / kinect_bridge 挂了都会被拉回来。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
PY = PYW if os.path.exists(PYW) else sys.executable
LOG = os.path.join(ROOT, "keeper.log")

out = open(LOG, "a", encoding="utf-8", errors="replace")
err = subprocess.STDOUT

flags = 0
if os.name == "nt":
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

p = subprocess.Popen(
    [PY, "-u", os.path.join(ROOT, "vision_watchdog_server.py")],
    cwd=ROOT, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
    creationflags=flags, close_fds=True,
)
print(f"keeper 已启动 PID={p.pid}（脱离父进程，独立常驻）")
print("它会依次拉起 tower / yolo / bridge / vtuber，约 15-30 秒后塔台可访问")
