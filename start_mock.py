# -*- coding: utf-8 -*-
"""以脱离父进程的方式启动 mock_tello.py，使其能长期独立常驻。"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
LOG = os.path.join(ROOT, "mock_tello.log")
ERR = os.path.join(ROOT, "mock_tello.err")

out = open(LOG, "w", encoding="utf-8", errors="replace")
err = open(ERR, "w", encoding="utf-8", errors="replace")

flags = 0
if os.name == "nt":
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

p = subprocess.Popen(
    [PY, "-u", os.path.join(ROOT, "mock_tello.py")],
    cwd=ROOT, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
    creationflags=flags, close_fds=True,
)
print(f"mock_tello 已启动 PID={p.pid}（脱离父进程，独立常驻）")
print(f"日志: {LOG}")
