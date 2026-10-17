#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟 Tello —— 在完全不起飞真机的前提下，把塔台的视频/遥控/巡逻链路跑通。

它假装自己是 192.168.10.1 上的那台无人机：
  - 监听 UDP 8889，按 SDK 1.3 的口径回 command/battery?/takeoff/rc/... 等命令
  - streamon 之后用 ffmpeg 把画面编码成裸 H.264，推 UDP 11111（和真机完全同格式）
  - 维护一个模拟位姿并按 rc 积分，写到 kinect_runtime/mock_uav_pose.json，
    充当"视觉反馈"，让 AI 巡逻闭环能被验证

视频画面默认回放 Kinect 彩图（加 SIM 水印），没有 Kinect 时自动降级到合成图案。

用法：
    python mock_tello.py                # 起模拟器
    python mock_tello.py --no-video     # 只模拟命令通道，不推视频
    python mock_tello.py --grounded     # 完全拒绝 takeoff（最严格的安全模式）
"""

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG = "ffmpeg"

ROOT = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.join(ROOT, "tower_ui", "kinect_runtime")
POSE_PATH = os.path.join(RUNTIME, "mock_uav_pose.json")

CMD_PORT = 8889
VIDEO_PORT = 11111
VIDEO_W, VIDEO_H, VIDEO_FPS = 960, 540, 30

_lock = threading.Lock()
_state = {
    "battery": 82,
    "flying": False,
    "streaming": False,
    "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0,   # 米 / 度
    "rc": [0, 0, 0, 0],
    "last_rc_ts": 0.0,
    "cmd_log": [],
    "takeoff_count": 0,
}


# ---------------------------------------------------------------- 视频推流
def _read_frame():
    """优先回放 Kinect 彩图；不可用时生成一张会动的合成画面"""
    img = None
    try:
        if cv2 is not None:
            data = np.fromfile(os.path.join(RUNTIME, "color.jpg"), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    except OSError:
        img = None
    if img is None and cv2 is not None:
        img = np.zeros((VIDEO_H, VIDEO_W, 3), np.uint8)
        t = time.time()
        cv2.rectangle(img, (0, 0), (VIDEO_W, VIDEO_H), (40, 30, 25), -1)
        for i in range(0, VIDEO_W, 60):
            cv2.line(img, (i, 0), (i, VIDEO_H), (55, 45, 38), 1)
        for j in range(0, VIDEO_H, 60):
            cv2.line(img, (0, j), (VIDEO_W, j), (55, 45, 38), 1)
        cx = int(VIDEO_W / 2 + math.sin(t) * 200)
        cv2.circle(img, (cx, VIDEO_H // 2), 45, (60, 150, 255), -1)
    if cv2 is not None and img is not None:
        if img.shape[1] != VIDEO_W or img.shape[0] != VIDEO_H:
            img = cv2.resize(img, (VIDEO_W, VIDEO_H))
        with _lock:
            st = dict(_state)
        _overlay(img, st)
    return img


def _overlay(img, st):
    tag = "SIMULATED" if not st["flying"] else "SIMULATED-FLYING"
    cv2.putText(img, "MOCK TELLO - " + tag, (14, 34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (60, 150, 255), 2)
    cv2.putText(img, time.strftime("%H:%M:%S"), (14, 64),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 220, 200), 2)
    cv2.putText(img, f"bat {st['battery']}%  pos({st['x']:.2f},{st['y']:.2f},{st['z']:.2f})  yaw {st['yaw']:.0f}",
                (14, VIDEO_H - 46), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 220, 200), 1)
    cv2.putText(img, f"rc {' '.join(str(v) for v in st['rc'])}",
                (14, VIDEO_H - 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 220, 200), 1)


def video_thread(stop_event):
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{VIDEO_W}x{VIDEO_H}", "-r", str(VIDEO_FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
           "-pix_fmt", "yuv420p", "-f", "h264",
           f"udp://127.0.0.1:{VIDEO_PORT}?pkt_size=1316"]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except FileNotFoundError:
        print(f"[mock] ffmpeg 起不来（{FFMPEG}），视频推流停用")
        return
    print(f"[mock] 视频推流已启动 -> udp://127.0.0.1:{VIDEO_PORT}")
    interval = 1.0 / VIDEO_FPS
    try:
        while not stop_event.is_set():
            t0 = time.time()
            img = _read_frame()
            if img is not None:
                try:
                    proc.stdin.write(img.tobytes())
                except (BrokenPipeError, OSError):
                    break
            dt = interval - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
    print("[mock] 视频推流已停止")


# ---------------------------------------------------------------- 位姿积分
def physics_thread(stop_event):
    """按 rc 积分模拟运动（只在'起飞'后动），并把位姿写文件当视觉反馈"""
    while not stop_event.is_set():
        time.sleep(0.2)
        with _lock:
            if not _state["flying"]:
                continue
            lr, fb, ud, yaw = _state["rc"]
            # 超过 1.5 秒没收到 rc 就悬停，逼近真机的 deadman 行为
            if time.time() - _state["last_rc_ts"] > 1.5:
                _state["rc"] = [0, 0, 0, 0]
                lr = fb = ud = yaw = 0
            dt = 0.2
            # Kinect camera frame (align with tello_patrol / patrol_waypoints.json):
            # x right+, y down+, z forward+; rc a=right b=forward c=up
            _state["yaw"] = (_state["yaw"] + yaw * 0.35 * dt) % 360
            _state["x"] += lr * 0.006 * dt
            _state["z"] += fb * 0.006 * dt
            _state["y"] -= ud * 0.005 * dt  # c+ up => y decreases
            _state["x"] = max(-3.0, min(3.0, _state["x"]))
            _state["y"] = max(-3.0, min(1.0, _state["y"]))
            _state["z"] = max(0.2, min(5.0, _state["z"]))
            pose = {"x": round(_state["x"], 3), "y": round(_state["y"], 3),
                    "z": round(_state["z"], 3), "yaw": round(_state["yaw"], 1),
                    "flying": True, "ts": time.time()}
        try:
            tmp = POSE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(pose, f)
            os.replace(tmp, POSE_PATH)
        except OSError:
            pass


# ---------------------------------------------------------------- 命令处理
def handle(cmd, addr, reply, video_ctl, grounded):
    cmd = cmd.strip()
    with _lock:
        _state["cmd_log"].append((time.strftime("%H:%M:%S"), cmd))
        if len(_state["cmd_log"]) > 200:
            _state["cmd_log"] = _state["cmd_log"][-200:]

    if cmd in ("command", "sdk on", "streamoff ack") or cmd.startswith("command"):
        reply(b"ok")
    elif cmd == "battery?":
        reply(str(_state["battery"]).encode())
    elif cmd == "time?":
        reply(b"12s")
    elif cmd == "speed?":
        reply(b"10")
    elif cmd == "wifi?":
        reply(b"90")
    elif cmd == "sn?":
        reply(b"0TQZK7SEDA0MOCK")
    elif cmd == "sdk?":
        reply(b"34")
    elif cmd == "height?":
        reply(b"0dm")
    elif cmd == "temp?":
        reply(b"66~72C")
    elif cmd == "baro?":
        reply(b"1013.2")
    elif cmd == "tof?":
        reply(b"10")
    elif cmd == "attitude?":
        with _lock:
            reply(f"pitch:0;roll:0;yaw:{int(_state['yaw'])}".encode())
    elif cmd == "acceleration?":
        reply(b"agx:0.00;agy:0.00;agz:9.81")
    elif cmd == "motor?":
        reply(b"0;0;0;0")
    elif cmd == "streamon":
        with _lock:
            _state["streaming"] = True
        video_ctl["start"]()
        reply(b"ok")
        print(f"[{time.strftime('%H:%M:%S')}] streamon -> 开始推流")
    elif cmd == "streamoff":
        with _lock:
            _state["streaming"] = False
        video_ctl["stop"]()
        reply(b"ok")
        print(f"[{time.strftime('%H:%M:%S')}] streamoff -> 停止推流")
    elif cmd == "takeoff":
        if grounded:
            reply(b"error auto rejected (grounded mode)")
            print(f"[{time.strftime('%H:%M:%S')}] takeoff 被拒绝（grounded 安全模式）")
        else:
            with _lock:
                if _state["flying"]:
                    reply(b"error already in air")
                else:
                    _state["flying"] = True
                    _state["takeoff_count"] += 1
                    # start in front of Kinect so first waypoint is reachable
                    _state["x"], _state["y"], _state["z"] = 0.0, -1.0, 0.8
                    reply(b"ok")
                    print(f"[{time.strftime('%H:%M:%S')}] takeoff -> 模拟升空（不影响任何真机）")
    elif cmd == "land":
        with _lock:
            _state["flying"] = False
            _state["rc"] = [0, 0, 0, 0]
        try:
            with open(POSE_PATH + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"x": round(_state["x"], 3), "y": round(_state["y"], 3),
                           "z": 0.0, "yaw": round(_state["yaw"], 1),
                           "flying": False, "ts": time.time()}, f)
            os.replace(POSE_PATH + ".tmp", POSE_PATH)
        except OSError:
            pass
        reply(b"ok")
        print(f"[{time.strftime('%H:%M:%S')}] land -> 已落地")
    elif cmd == "emergency":
        with _lock:
            _state["flying"] = False
            _state["rc"] = [0, 0, 0, 0]
        video_ctl["stop"]()
        reply(b"ok")
        print(f"[{time.strftime('%H:%M:%S')}] emergency -> 急停")
    elif cmd.startswith("rc "):
        parts = cmd.split()[1:5]
        vals = []
        for v in parts:
            try:
                vals.append(max(-100, min(100, int(float(v)))))
            except ValueError:
                vals.append(0)
        while len(vals) < 4:
            vals.append(0)
        with _lock:
            _state["rc"] = vals
            _state["last_rc_ts"] = time.time()
            flying = _state["flying"]
        # 真机未起飞时 rc 不产生位移，这里也如实模拟：只在起飞后才动
        if not flying:
            print(f"[{time.strftime('%H:%M:%S')}] rc {vals} 已收到但未起飞 -> 不产生位移（与真机一致）")
        else:
            print(f"[{time.strftime('%H:%M:%S')}] rc {vals} -> 模拟移动")
        # Tello 对 rc 不回 "ok"（fire-and-forget），这里也保持沉默
    else:
        reply(b"error unknown command")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-video", action="store_true", help="只模拟命令通道")
    ap.add_argument("--grounded", action="store_true", help="拒绝一切 takeoff")
    args = ap.parse_args()

    stop = threading.Event()
    video_ctl = {"start": lambda: None, "stop": lambda: None}
    vstop = None
    vthread = None

    def _start_video():
        nonlocal vstop, vthread
        if args.no_video or (vthread and vthread.is_alive()):
            return
        vstop = threading.Event()
        vthread = threading.Thread(target=video_thread, args=(vstop,), daemon=True)
        vthread.start()

    def _stop_video():
        nonlocal vstop
        if vstop:
            vstop.set()

    video_ctl["start"] = _start_video
    video_ctl["stop"] = _stop_video

    threading.Thread(target=physics_thread, args=(stop,), daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        try:
            # Windows 特有：UDP socket 收到 ICMP port-unreachable 后会让
            # 后续 recvfrom 全部抛 ConnectionResetError，把这个行为关掉
            sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        except Exception:
            pass
    sock.bind(("", CMD_PORT))
    sock.settimeout(0.5)

    mode = "GROUNDED(禁止起飞)" if args.grounded else "允许模拟起飞"
    print("=" * 62)
    print("模拟 Tello 已启动（不会驱动任何真机）")
    print(f"  命令端口 UDP {CMD_PORT}   视频输出 UDP {VIDEO_PORT}")
    print(f"  视频分辨率 {VIDEO_W}x{VIDEO_H}@{VIDEO_FPS}  安全模式: {mode}")
    print(f"  位姿反馈文件: {POSE_PATH}")
    print("  停止请按 Ctrl+C")
    print("=" * 62)

    try:
        while True:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except (ConnectionResetError, OSError):
                # Windows: 对端端口已关闭，忽略继续等下一个命令
                continue
            handle(data.decode("ascii", "ignore"), addr,
                  lambda b: sock.sendto(b, addr), video_ctl, args.grounded)
    except KeyboardInterrupt:
        print("\n[mock] 已停止")
    finally:
        stop.set()
        _stop_video()
        sock.close()


if __name__ == "__main__":
    main()
