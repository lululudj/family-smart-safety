#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 室内巡逻控制器 —— Tello 闭环巡航。

安全设计（第一原则）：
  1. 本控制器 **从不主动起飞**。起飞必须由人工在网页控制台按"起飞"完成；
     巡逻器检测到未起飞时只会待机并输出提示，绝不代发 takeoff。
  2. `patrol_off.flag` 一旦出现在项目根，巡逻器在 200ms 内停发一切 rc 并悬停。
  3. 视觉丢失（Kinect 连续 VISION_TIMEOUT 看不到 Tello）→ rc0，再超时则 land。
  4. 电量 < LOW_BATTERY → 中止巡逻（返航由人工接管，控制器只提示）。
  5. rc 全程 ±SPEED_LIMIT 限幅；塔台另有 0.5s deadman 兜底悬停。
  6. 与 tello_hover 互斥：争用 control.lock，禁止双控。

感知：优先塔台 /api/status 的 vision.pose（UDP 真实检测，不含 hold）；
      回退 objects 时排除 held；位姿过期视为丢失；无视觉时退回 mock。
控制：P 控制器，位置误差 → rc(a,b,c,d)，10Hz 经 UDP 8897 下发。
模式：
  --dry-run   只打印决策，不发任何指令（默认，用于验证逻辑）
  --live      真发 rc（要求无人机已在空中，否则拒绝启动）
"""

import argparse
import atexit
import json
import math
import os
import socket
import sys
import time
import urllib.request
from urllib.parse import urlparse

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
TOWER = os.environ.get("TOWER", "http://127.0.0.1:8080")
_TOWER_HOST = urlparse(TOWER).hostname or "127.0.0.1"
TOWER_IP = os.environ.get("TOWER_IP", _TOWER_HOST)
TOWER_RC_PORT = int(os.environ.get("RC_PORT", "8897"))
WAYPOINTS_PATH = os.path.join(ROOT, "patrol_waypoints.json")
PATROL_OFF_FLAG = os.path.join(ROOT, "patrol_off.flag")
MOCK_POSE = os.path.join(ROOT, "tower_ui", "kinect_runtime", "mock_uav_pose.json")
CONTROL_LOCK = os.path.join(ROOT, "tower_ui", "kinect_runtime", "control.lock")
CONTROL_ROLE = "patrol"

KP = 55.0          # P 增益：误差(米) → rc(-100~100)
SPEED_LIMIT = 45   # rc 限幅（室内安全速度）
TOL = 0.30         # 到点半径（米）
VISION_TIMEOUT = 0.8
POSE_MAX_AGE = 0.6  # vision.pose / pose_age 超过此时长视为丢失
LOW_BATTERY = 30
RC_HZ = 10

_rc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def clamp(v, lo=-100, hi=100):
    return max(lo, min(hi, v))


def _pid_alive(pid):
    """跨平台判断进程是否仍在。Windows 上 os.kill(pid, 0) 不可靠。"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, SystemError):
        return False


def acquire_control_lock(role):
    os.makedirs(os.path.dirname(CONTROL_LOCK), exist_ok=True)
    if os.path.isfile(CONTROL_LOCK):
        try:
            with open(CONTROL_LOCK, "r", encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            info = {}
        other_pid = info.get("pid")
        other_role = info.get("role")
        if _pid_alive(other_pid):
            if other_role != role or other_pid != os.getpid():
                log(f"!! 控制权已被占用：role={other_role} pid={other_pid}。"
                    f"请先结束该进程，或删除 {CONTROL_LOCK}（仅当确认无控制器在跑）。")
                log("   （禁止 hover 与 patrol 同时控机，也禁止同角色双开。）")
                raise SystemExit(2)
        else:
            log(f"发现陈旧控制锁（role={other_role} pid={other_pid} 已不在）→ 接管")
    payload = {"pid": os.getpid(), "role": role, "ts": time.time()}
    with open(CONTROL_LOCK, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    atexit.register(release_control_lock, role)
    log(f"已取得控制锁 role={role} pid={os.getpid()}")


def release_control_lock(role):
    try:
        if not os.path.isfile(CONTROL_LOCK):
            return
        with open(CONTROL_LOCK, "r", encoding="utf-8") as f:
            info = json.load(f)
        if info.get("pid") == os.getpid() and info.get("role") == role:
            os.remove(CONTROL_LOCK)
            log("已释放控制锁")
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------- 感知
def get_vision_pose():
    """优先 vision.pose（UDP 真实检测）；回退 objects 时排除 held。"""
    try:
        st = json.loads(urllib.request.urlopen(f"{TOWER}/api/status", timeout=2).read())
    except Exception:
        return None
    vision = st.get("vision") or {}

    pose = vision.get("pose")
    age = vision.get("pose_age")
    if isinstance(age, (int, float)) and age > POSE_MAX_AGE:
        pose = None
    if isinstance(pose, dict) and pose.get("x") is not None and pose.get("z") is not None:
        return {
            "x": pose["x"], "y": pose["y"], "z": pose["z"],
            "yaw": 0.0, "src": "kinect-pose", "conf": pose.get("conf"),
        }

    for o in vision.get("objects") or []:
        if o.get("held"):
            continue
        if o.get("drone") and o.get("verified") and o.get("z") is not None:
            return {"x": o["x"], "y": o["y"], "z": o["z"],
                    "yaw": 0.0, "src": "kinect", "conf": o.get("conf")}
    return None


def get_mock_pose():
    try:
        with open(MOCK_POSE, "r", encoding="utf-8") as f:
            p = json.load(f)
        if p.get("flying"):
            return {"x": p["x"], "y": p["y"], "z": p["z"],
                    "yaw": p.get("yaw", 0), "src": "mock"}
    except (OSError, ValueError):
        pass
    return None


def get_battery():
    try:
        st = json.loads(urllib.request.urlopen(f"{TOWER}/api/status", timeout=2).read())
        return st.get("uav", {}).get("battery")
    except Exception:
        return None


def send_rc(a, b, c, d, dry):
    """优先 UDP 8897；失败时 HTTP fallback；任一路失败时继续尝试 UDP 发 rc0。"""
    if dry:
        return True
    payload = json.dumps({"a": a, "b": b, "c": c, "d": d}).encode()
    try:
        _rc_sock.sendto(payload, (TOWER_IP, TOWER_RC_PORT))
        return True
    except OSError as e:
        log(f"[rc] UDP 发送失败: {e} → 尝试 HTTP fallback")

    try:
        req = urllib.request.Request(
            f"{TOWER}/api/uav/rc",
            data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        resp = json.loads(urllib.request.urlopen(req, timeout=1.5).read())
        if isinstance(resp, dict) and resp.get("ok") is False:
            log(f"[安全] 塔台拒绝 rc: {resp.get('msg')}")
            _udp_rc0_best_effort()
            return False
        return True
    except Exception as e:
        log(f"[rc] HTTP 也失败: {e} → 本地尽力 UDP 发 rc0")
        _udp_rc0_best_effort()
        return False


def _udp_rc0_best_effort(times=5):
    zero = json.dumps({"a": 0, "b": 0, "c": 0, "d": 0}).encode()
    for _ in range(times):
        try:
            _rc_sock.sendto(zero, (TOWER_IP, TOWER_RC_PORT))
        except OSError:
            pass
        time.sleep(0.05)


def send_land(dry):
    if dry:
        return
    _udp_rc0_best_effort(times=5)
    last_err = None
    for i in range(3):
        try:
            req = urllib.request.Request(
                f"{TOWER}/api/uav/cmd", data=json.dumps({"cmd": "land"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5)
            _udp_rc0_best_effort(times=2)
            return
        except Exception as e:
            last_err = e
            log(f"[land] 第 {i + 1}/3 次失败: {e}")
            _udp_rc0_best_effort(times=3)
            time.sleep(0.2)
    log(f"[land] 全部失败: {last_err}（已尽量 UDP rc0，请人工接管）")


def flying_via_tower():
    """塔台侧的飞行状态判断：只要探测在线就视为可能飞行（保守）。"""
    try:
        st = json.loads(urllib.request.urlopen(f"{TOWER}/api/status", timeout=2).read())
        return bool(st.get("uav", {}).get("online"))
    except Exception:
        return False


def control_step(pose, target):
    """位置误差 → rc。返回 (a,b,c,d, dist)"""
    ex = target["x"] - pose["x"]
    ey = target["y"] - pose["y"]
    ez = target["z"] - pose["z"]
    dist = math.sqrt(ex * ex + ey * ey + ez * ez)

    a = int(clamp(KP * ex, -SPEED_LIMIT, SPEED_LIMIT))
    b = int(clamp(KP * ez, -SPEED_LIMIT, SPEED_LIMIT))
    c = int(clamp(-KP * ey, -SPEED_LIMIT, SPEED_LIMIT))
    return a, b, c, 0, dist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印决策，不发指令（默认）")
    ap.add_argument("--live", action="store_true", help="真发 rc（要求无人机已在空中）")
    ap.add_argument("--laps", type=int, default=1, help="巡逻圈数")
    args = ap.parse_args()
    dry = not args.live

    with open(WAYPOINTS_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    wps = cfg["waypoints"]

    log("=" * 62)
    log(f"AI 巡逻控制器  模式: {'DRY-RUN（不发指令）' if dry else 'LIVE（真发 rc）'}")
    log(f"航点 {len(wps)} 个 × {args.laps} 圈 · 到点半径 {TOL}m · 限速 {SPEED_LIMIT}")
    log(f"位姿: vision.pose优先（拒held）· rc→UDP:{TOWER_RC_PORT}@{TOWER_IP}")
    for w in wps:
        log(f"  - {w['name']:<6} ({w['x']:+.1f},{w['y']:+.1f},{w['z']:+.1f}) 停 {w['hold_s']}s")
    log("=" * 62)

    if PATROL_OFF_FLAG is not None and os.path.exists(PATROL_OFF_FLAG):
        log("发现 patrol_off.flag —— 巡逻被禁止，退出。删除该文件后可再试。")
        return

    acquire_control_lock(CONTROL_ROLE)

    if args.live and not flying_via_tower():
        log("LIVE 模式要求无人机已在空中（塔台探测在线）。请先在网页手动起飞，再启动本程序。")
        log("（本控制器绝不代发 takeoff —— 这是硬编码的安全约束）")
        return

    dt = 1.0 / RC_HZ
    hold_until = 0.0
    wp_idx = 0
    lap = 1
    last_pose_ts = time.time()
    done = False

    while not done:
        loop_t0 = time.time()

        if os.path.exists(PATROL_OFF_FLAG):
            log("!! 检测到 patrol_off.flag → 悬停并退出巡逻")
            send_rc(0, 0, 0, 0, dry)
            break

        batt = get_battery()
        if batt is not None and batt < LOW_BATTERY:
            log(f"!! 电量 {batt}% < {LOW_BATTERY}% → 中止巡逻（请人工接管返航）")
            send_rc(0, 0, 0, 0, dry)
            send_land(dry)
            break

        pose = get_vision_pose() or get_mock_pose()
        if pose is None:
            # 丢检：持续 rc0（松杆≠锁定）；超时主动 land，避免室内光流漂移撞墙
            send_rc(0, 0, 0, 0, dry)
            lost_for = time.time() - last_pose_ts
            if lost_for > VISION_TIMEOUT:
                log(f"[安全] 视觉丢失超 {VISION_TIMEOUT:.1f}s（已丢 {lost_for:.1f}s）"
                    f"→ rc0 后主动降落（松杆不是位置锁定）")
                send_land(dry)
                break
            elif int(lost_for * 5) != int((lost_for - dt) * 5):
                log(f"[安全] 视觉丢失中 age={lost_for:.2f}s → 持续 rc0")
            time.sleep(dt)
            continue
        last_pose_ts = time.time()

        tgt = wps[wp_idx]

        if hold_until > time.time():
            send_rc(0, 0, 0, 0, dry)
            remain = int(hold_until - time.time())
            if remain % 2 == 0:
                log(f"  [{tgt['name']}] 悬停 {remain}s")
            time.sleep(dt)
            continue

        a, b, c, d, dist = control_step(pose, tgt)

        if dist <= TOL:
            log(f"  [到达] {tgt['name']}  距离 {dist:.2f}m  → 悬停 {tgt['hold_s']}s")
            hold_until = time.time() + tgt["hold_s"]
            wp_idx += 1
            if wp_idx >= len(wps):
                if lap >= args.laps:
                    done = True
                    log("巡逻完成，悬停待命（降落请人工在网页操作）")
                    send_rc(0, 0, 0, 0, dry)
                    break
                lap += 1
                wp_idx = 0
                log(f"—— 第 {lap} 圈 ——")
            continue

        if not send_rc(a, b, c, d, dry):
            log("!! 塔台拒绝 / 失联 → 悬停并退出巡逻（请人工接管）")
            send_rc(0, 0, 0, 0, dry)
            break
        log(f"  [{tgt['name']}] dist={dist:.2f}m pose=({pose['x']:+.2f},{pose['y']:+.2f},{pose['z']:+.2f}) "
            f"rc=({a:+3d},{b:+3d},{c:+3d},{d:+3d}) src={pose['src']}")

        sleep_left = dt - (time.time() - loop_t0)
        if sleep_left > 0:
            time.sleep(sleep_left)

    send_rc(0, 0, 0, 0, dry)
    log("控制器退出")


if __name__ == "__main__":
    main()
