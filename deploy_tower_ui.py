#!/usr/bin/env python3
"""部署塔台仪表盘 v0.3 到 Orange Pi（含 Tello 视频链路）"""
import sys
import paramiko

HOSTS = ["192.168.18.181", "192.168.18.182"]
USER, PWD = "orangepi", "orangepi"
LOCAL = r"E:\全家智能安全调度系统\tower_ui"

def connect():
    last = None
    for h in HOSTS:
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            cli.connect(h, username=USER, password=PWD, timeout=8)
            print(f"[connected via {h}]")
            return cli
        except Exception as e:
            last = e
    raise SystemExit(f"all hosts unreachable: {last}")

def run(cli, cmd, timeout=120):
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    print(f"$ {cmd}")
    if out: print(out)
    if err and "密码" not in err: print("[stderr]", err)
    print("---")

def main():
    cli = connect()
    sftp = cli.open_sftp()

    # 1. 上传文件
    sftp.put(LOCAL + r"\server.py", "/home/orangepi/tower_ui/server.py")
    sftp.put(LOCAL + r"\web\index.html", "/home/orangepi/tower_ui/web/index.html")
    print("[uploaded] server.py + web/index.html")

    # 2. 安装 flask（仅首次）
    run(cli, "python3 -c 'import flask' 2>/dev/null || python3 -m pip install --user flask 2>&1 | tail -2")

    # 3. Tello 热点优先自动连接（wlan0 空闲时优先连 Tello，遇不到就回落家里 WiFi）
    run(cli, "echo orangepi | sudo -S nmcli con mod TELLO-5896C7 connection.autoconnect yes connection.autoconnect-priority 50 2>&1; "
             "echo orangepi | sudo -S nmcli con mod CU-A696-5G connection.autoconnect-priority 5 2>&1; echo nm-ok")
    run(cli, "nmcli -t -f NAME,AUTOCONNECT,AUTOCONNECT-PRIORITY con show | grep -iE 'tello|A696'")

    # 4. 重启服务
    run(cli, "pkill -f 'python3 server.py' 2>/dev/null; sleep 1; "
             "cd ~/tower_ui && nohup python3 server.py > ui.log 2>&1 & sleep 3; echo restarted")
    run(cli, "curl -s localhost:8080/api/status | head -c 600; echo")

    sftp.close()
    cli.close()

if __name__ == "__main__":
    main()
