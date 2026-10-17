#!/usr/bin/env python3
"""部署 v0.3.1 并端到端实测 Tello 视频链路"""
import json
import paramiko

HOST, USER, PWD = "192.168.18.181", "orangepi", "orangepi"

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=8)
print(f"[connected {HOST}]")


def run(cmd, t=40):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=t)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    return out, err


# 1. 上传
sftp = cli.open_sftp()
sftp.put(r"E:\全家智能安全调度系统\tower_ui\server.py", "/home/orangepi/tower_ui/server.py")
sftp.close()
print("[uploaded server.py v0.3.1]")

# 2. 重启（注意 serve[r].py 防自杀匹配）
run("kill $(pgrep -f 'serve[r].py') 2>/dev/null; echo killed")
run("cd ~/tower_ui && setsid -f python3 server.py >> ui.log 2>&1 < /dev/null; sleep 1; echo started")
run("sleep 3; pgrep -af 'serve[r].py' | head -1")

# 3. 开启视频（新逻辑：以视频数据包为判据）
out, err = run("curl -s -m 30 -X POST localhost:8080/api/uav/video/start", t=60)
print("video start ->", out or err)

# 4. 等 ffmpeg 预热后拉流验证
run("sleep 6")
out, _ = run("pgrep -af 'ffmpeg' | head -2")
print("ffmpeg:", out or "(无进程)")
out, _ = run("curl -s -m 8 -o /dev/null -w 'bytes=%{size_download} http=%{http_code}' http://localhost:8080/video/mjpg", t=30)
print("mjpg pull:", out)

# 5. 状态 + 错误检查
out, _ = run("curl -s -m 5 localhost:8080/api/status")
try:
    d = json.loads(out)
    print("uav:", d["uav"])
    print("uav_video:", d["uav_video"])
except Exception as e:
    print("status parse fail:", e, out[:200])

cli.close()
