#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 悬停锁定控制器 —— 让 Tello 定在一点不动。

为什么需要它：
  Tello 没有 GPS，机载下视光流在地板纹理不足时会持续漂移，"松杆"根本停不住。
  唯一的办法是外部视觉闭环：Kinect 每帧量出 Tello 的相机坐标 → 控制器算偏移
  → 用 rc 反向纠正，10Hz 持续下发，把它按回目标点。

为什么之前的巡逻器会"到处乱飘"（根因，已修）：
  1. 位姿来自塔台 2 秒一次的低频 HTTP 上报 —— 等于拿 2 秒前的位置纠偏；
  2. 视觉丢失时 DRONE_HOLD 会把 4 秒前的幽灵坐标回灌进来，控制器照旧坐标推。
  两者叠加，闭环必然发散。本控制器改用 kinect_yolo3d 直推的 UDP 位姿
  （约 5fps、只含真实检测），rc 也走 UDP 下发，全程不产生 TCP 连接
  （避免 TIME_WAIT 堆积 —— 历史上 12fps 时就因端口耗尽拖垮过 USB）。

摆放要求（重要）：
  Tello 机头朝「远离 Kinect 的方向」摆放。坐标映射按此约定：
    相机 x 向右 → rc a（向右）
    相机 z 变远 → rc b（向前）
    相机 y 向下 → rc c（向上，取负）

安全网：
  1. 默认不自动起飞；只有显式 --takeoff 才发 takeoff，且起飞前必须先在视觉里
     看到 Tello（无视觉 = 无法闭环 = 盲飞，直接拒绝）。
  2. 位姿失效 > POSE_TIMEOUT → 立即 rc 0 松杆；连续丢失 > LOST_ABORT 秒 → 中止。
  3. --takeoff 后的等待窗口内，位姿连续丢失 > TAKEOFF_LOST_S → 立刻 rc0 + land 退出
     （不再盲等满 --delay 再进主闭环）。
  4. 单帧偏差 > MAX_ERR（识别跳变 / 飞机被拿起）→ 暂停纠偏，只发 rc 0。
  5. rc 限幅 ±LIMIT；死区内不发指令，避免抖动自激。
  6. 塔台失联 → 立即中止（rc 发不出去 = 失控）。
  7. patrol_off.flag / hover_off.flag 出现 → 立即悬停退出。
  8. 与 tello_patrol 互斥：争用 control.lock，禁止双控。

用法：
  python tello_hover.py --dry-run             # 只打印决策，不发指令
  python tello_hover.py --live                # 飞机已在空中：立即锁定当前位置
  python tello_hover.py --live --takeoff      # 自动起飞 → 延时 → 锁定悬停
"""

import argparse
import atexit
import json
import math
import os
import socket
import sys
import threading
import time
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
TOWER_IP = os.environ.get("TOWER_IP", "127.0.0.1")
TOWER_HTTP = os.environ.get("TOWER", f"http://{TOWER_IP}:8080")
POSE_PORT = int(os.environ.get("HOVER_PORT", "8899"))    # 收 kinect_yolo3d 位姿
TOWER_RC_PORT = int(os.environ.get("RC_PORT", "8897"))   # 发 rc 给塔台
HOVER_OFF_FLAG = os.path.join(ROOT, "hover_off.flag")
PATROL_OFF_FLAG = os.path.join(ROOT, "patrol_off.flag")
CONTROL_LOCK = os.path.join(ROOT, "tower_ui", "kinect_runtime", "control.lock")
CONTROL_ROLE = "hover"

KP_XY = 70.0        # 水平 P 增益：误差(米) → rc
KP_Z = 55.0         # 垂直 P 增益（Tello 垂直更敏感，压低一点）
LIMIT = 30          # rc 限幅（室内安全速度）
DEADBAND = 0.10     # 死区（米）：误差小于此值不发修正指令
EMA = 0.45          # 位姿指数平滑系数（抑制单帧框抖）
POSE_TIMEOUT = 0.8  # 位姿超过这么久没更新 → 视为丢失 → 松杆
LOST_ABORT = 0.8    # 连续丢失这么久 → 主动降落（已从 1.5s 压短，减少松杆漂移撞墙）
TAKEOFF_LOST_S = 0.6  # 起飞等待窗口内连续丢检超过此时长 → 立刻落机退出
MAX_ERR = 1.2
JUMP_MAX = 0.6
JUMP_TOL = 3
RC_HZ = 10


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def clamp(v, lo, hi):
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
    """争用 control.lock；另一角色进程仍在则拒绝启动。"""
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


POSE = {"ts": 0.0, "x": None, "y": None, "z": None, "conf": None}
_pose_lock = threading.Lock()


def _pose_rx_loop():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.ioctl(socket.SIO_UDP_CONNRESET, False)
    except Exception:
        pass
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", POSE_PORT))
    except OSError as e:
        log(f"!! 位姿监听绑定 {POSE_PORT} 失败: {e}")
        return
    log(f"位姿监听已绑定 0.0.0.0:{POSE_PORT}")
    while True:
        try:
            data, _ = s.recvfrom(2048)
        except OSError:
            time.sleep(0.5)
            continue
        try:
            p = json.loads(data.decode("utf-8", "ignore"))
        except ValueError:
            continue
        with _pose_lock:
            POSE.update(ts=time.time(), x=p.get("x"), y=p.get("y"),
                        z=p.get("z"), conf=p.get("conf"))


def get_pose():
    with _pose_lock:
        if (time.time() - POSE["ts"]) < POSE_TIMEOUT and POSE["x"] is not None:
            return dict(POSE)
    return None


def pose_age():
    with _pose_lock:
        return time.time() - POSE["ts"] if POSE["ts"] else -1.0


_rc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def send_rc(a, b, c, d, dry):
    if dry:
        return True
    try:
        _rc_sock.sendto(json.dumps({"a": a, "b": b, "c": c, "d": d}).encode(),
                        (TOWER_IP, TOWER_RC_PORT))
        return True
    except OSError as e:
        log(f"[rc] 发送失败: {e}")
        return False


def http_post(path, payload, timeout=8):
    req = urllib.request.Request(f"{TOWER_HTTP}{path}",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def tower_status():
    try:
        return json.loads(urllib.request.urlopen(f"{TOWER_HTTP}/api/status", timeout=1.5).read())
    except Exception:
        return None


def blast_rc0(dry, times=5, gap=0.05):
    ok = True
    for _ in range(times):
        if not send_rc(0, 0, 0, 0, dry):
            ok = False
        time.sleep(gap)
    return ok


def land(dry, retries=3):
    """主动降落。先连发 rc0，再 HTTP land 短重试。"""
    blast_rc0(dry, times=5, gap=0.05)
    if dry:
        log("[dry-run] 跳过 land")
        return True
    last_err = None
    for i in range(retries):
        try:
            resp = http_post("/api/uav/cmd", {"cmd": "land"}, timeout=5)
            log(f"land → {resp}（第 {i + 1}/{retries} 次）")
            blast_rc0(dry, times=2, gap=0.05)
            return True
        except Exception as e:
            last_err = e
            log(f"[land] 第 {i + 1}/{retries} 次失败: {e} → 再发 rc0 后重试")
            blast_rc0(dry, times=3, gap=0.05)
            time.sleep(0.2)
    log(f"[land] 全部重试失败: {last_err}（已尽量连发 rc0，请人工急停/网页降落）")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印决策，不发指令（默认）")
    ap.add_argument("--live", action="store_true", help="真发 rc（默认 dry-run）")
    ap.add_argument("--takeoff", action="store_true", help="自动起飞，延时后锁定悬停")
    ap.add_argument("--delay", type=float, default=3.5, help="起飞后等待稳定的秒数（默认 3.5）")
    ap.add_argument("--hold", type=float, default=0.0, help="锁定保持秒数，0=一直保持")
    args = ap.parse_args()
    dry = not args.live

    log("=" * 64)
    log(f"AI 悬停锁定控制器   模式: {'DRY-RUN（不发指令）' if dry else 'LIVE（真发 rc）'}")
    log(f"kp_xy={KP_XY} kp_z={KP_Z} 限速±{LIMIT} 死区{DEADBAND}m "
        f"位姿: UDP:{POSE_PORT}  rc→塔台 UDP:{TOWER_RC_PORT}")
    log("=" * 64)

    if os.path.exists(PATROL_OFF_FLAG) or os.path.exists(HOVER_OFF_FLAG):
        log("发现 patrol_off.flag / hover_off.flag —— 已禁止，退出。")
        return

    acquire_control_lock(CONTROL_ROLE)

    threading.Thread(target=_pose_rx_loop, daemon=True, name="pose-rx").start()

    if args.takeoff:
        log("起飞前检查：视觉里能否看到 Tello…")
        ok = False
        for _ in range(48):
            if get_pose() is not None:
                ok = True
                break
            time.sleep(0.25)
        if not ok:
            log(f"!! 视觉看不到 Tello（{POSE_PORT} 无有效位姿）→ 拒绝自动起飞。"
                f"先确认 kinect_yolo3d 在跑并且能框到 Tello。")
            return
        if dry:
            log("[dry-run] 跳过 takeoff（不真起飞）")
        else:
            try:
                log(f"takeoff → {http_post('/api/uav/cmd', {'cmd': 'takeoff'}, timeout=10)}")
            except Exception as e:
                log(f"!! takeoff 失败: {e} → 中止")
                return
            log(f"起飞中，等待最多 {args.delay:.0f}s 让飞机稳定"
                f"（丢检连续 >{TAKEOFF_LOST_S:.1f}s 立即落机退出）…")
            t_end = time.time() + args.delay
            lost_since = None
            while time.time() < t_end:
                p = get_pose()
                if p is None:
                    if lost_since is None:
                        lost_since = time.time()
                        log(f"  [起飞观察] 无位姿 age={pose_age():.2f}s → 开始计时")
                    elif time.time() - lost_since >= TAKEOFF_LOST_S:
                        log(f"!! 起飞等待中视觉连续丢失 {TAKEOFF_LOST_S:.1f}s"
                            f" → rc0 + land，不进入悬停闭环")
                        land(dry)
                        return
                    else:
                        log(f"  [起飞观察] 无位姿 age={pose_age():.2f}s "
                            f"已丢 {time.time() - lost_since:.2f}s")
                else:
                    if lost_since is not None:
                        log("  [起飞观察] 位姿恢复")
                    lost_since = None
                    log(f"  [起飞观察] x={p['x']:+.2f} y={p['y']:+.2f} z={p['z']:.2f} "
                        f"conf={p.get('conf')}")
                time.sleep(0.15)
            if get_pose() is None:
                log("!! 起飞等待结束仍无有效位姿 → rc0 + land，不进入悬停闭环")
                land(dry)
                return

    target = None
    sx = sy = sz = None
    jump_n = 0
    lost_since = None
    lock_ref = None
    diverge_since = None
    tower_lost_since = None
    t_start = time.time()
    dt = 1.0 / RC_HZ
    last_batt_check = 0.0
    batt = None

    while True:
        loop_t0 = time.time()

        if os.path.exists(PATROL_OFF_FLAG) or os.path.exists(HOVER_OFF_FLAG):
            log("!! 检测到停止标志 → 悬停并退出")
            blast_rc0(dry)
            break

        if time.time() - last_batt_check >= 2.0:
            last_batt_check = time.time()
            st = tower_status()
            if st is None:
                if tower_lost_since is None:
                    tower_lost_since = time.time()
                    log("!! 读不到塔台状态 → rc 可能发不出去")
                elif time.time() - tower_lost_since > 5.0:
                    log("!! 塔台失联超 5s → 中止（继续发 rc 是无效的，飞机已失控）")
                    blast_rc0(dry)
                    break
            else:
                tower_lost_since = None
                batt = (st.get("uav") or {}).get("battery")
                if batt is not None and batt < 25:
                    log(f"!! 电量 {batt}% 过低 → 悬停并退出（请人工降落）")
                    blast_rc0(dry)
                    break

        pose = get_pose()

        if pose is None:
            if lost_since is None:
                lost_since = time.time()
                log(f"[安全] 位姿丢失（age={pose_age():.2f}s）→ 持续 rc0（松杆≠位置锁定，光流仍可能漂）")
            send_rc(0, 0, 0, 0, dry)
            if time.time() - lost_since > LOST_ABORT:
                log(f"!! 位姿连续丢失 {LOST_ABORT:.1f}s → 主动降落"
                    f"（rc0 只是停指令不是悬停锁定；超时必须 land）")
                land(dry)
                break
            time.sleep(dt)
            continue
        lost_since = None

        if sx is not None:
            jump = math.sqrt((pose["x"] - sx) ** 2 + (pose["y"] - sy) ** 2
                             + (pose["z"] - sz) ** 2)
            if jump > JUMP_MAX:
                jump_n += 1
                if jump_n <= JUMP_TOL:
                    log(f"[过滤] 位姿单帧跳变 {jump:.2f}m（疑似把别的物体认成 drone）"
                        f" → 丢弃本帧 {jump_n}/{JUMP_TOL}")
                    send_rc(0, 0, 0, 0, dry)
                    time.sleep(dt)
                    continue
            else:
                jump_n = 0

        if sx is None:
            sx, sy, sz = pose["x"], pose["y"], pose["z"]
        else:
            sx += EMA * (pose["x"] - sx)
            sy += EMA * (pose["y"] - sy)
            sz += EMA * (pose["z"] - sz)

        if target is None:
            target = {"x": sx, "y": sy, "z": sz}
            log(f"锁定悬停点  相机坐标 x={sx:+.2f} y={sy:+.2f} z={sz:+.2f} m"
                f"（conf={pose.get('conf')}）")
            send_rc(0, 0, 0, 0, dry)
            time.sleep(dt)
            continue

        ex = target["x"] - sx
        ey = target["y"] - sy
        ez = target["z"] - sz
        dist = math.sqrt(ex * ex + ey * ey + ez * ez)

        if dist > MAX_ERR:
            log(f"[安全] 偏差 {dist:.2f}m > {MAX_ERR}m（识别跳变 / 飞机被拿起？）→ 暂停纠偏")
            send_rc(0, 0, 0, 0, dry)
            time.sleep(dt)
            continue

        if lock_ref is None:
            lock_ref = dist
        if dist > 0.15 and dist > lock_ref + 0.35:
            if diverge_since is None:
                diverge_since = time.time()
                log(f"[安全] 偏差从 {lock_ref:.2f}m 涨到 {dist:.2f}m（疑似坐标符号反了）")
            elif time.time() - diverge_since > 1.2:
                log("!! 偏差持续变大 1.2s → 停手降落")
                land(dry)
                break
        else:
            diverge_since = None

        if dist <= DEADBAND:
            send_rc(0, 0, 0, 0, dry)
            log(f"  已锁定 dist={dist:.3f}m  dx={ex:+.3f} dy={ey:+.3f} dz={ez:+.3f}"
                f"  batt={batt}")
            time.sleep(dt)
            continue

        a = int(clamp(KP_XY * ex, -LIMIT, LIMIT))
        b = int(clamp(KP_XY * ez, -LIMIT, LIMIT))
        c = int(clamp(-KP_Z * ey, -LIMIT, LIMIT))
        if not send_rc(a, b, c, 0, dry):
            log("!! rc 发送异常 → 悬停退出")
            blast_rc0(dry)
            break
        log(f"  纠偏 dist={dist:.3f}m rc=({a:+3d},{b:+3d},{c:+3d}) "
            f"pose=({sx:+.2f},{sy:+.2f},{sz:+.2f}) conf={pose.get('conf')} batt={batt}")

        if args.hold and (time.time() - t_start) > args.hold:
            log(f"保持 {args.hold:.0f}s 完成，悬停退出")
            blast_rc0(dry)
            break

        sleep_left = dt - (time.time() - loop_t0)
        if sleep_left > 0:
            time.sleep(sleep_left)

    blast_rc0(dry)
    log("控制器退出")


if __name__ == "__main__":
    main()
