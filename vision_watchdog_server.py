#!/usr/bin/env python3
# 笔记本全链路主动守护：每 15 秒检查并自动拉起 bridge / 塔台 / yolo3d / VTuber
# 同时保留 8091 HTTP 接口（网页"重新连接"按钮调 /health 或 /restart）
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

PY = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
UV = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python312\Scripts\uv.exe"
PROJECT = r"E:\全家智能安全调度系统"
TOWER_UI = os.path.join(PROJECT, "tower_ui")
VTUBER = r"E:\OpenLLMVTuber"

SERVICES = {
    # name: (匹配命令行关键词, 进程名, 启动参数列表, 工作目录)
    # 采集层已由 C# bridge 换成 Python 直连（kinect_direct.py）：
    # 少一层进程、少一次落盘，且不再用 File.Replace —— 杜绝 ~RF*.TMP 残留堆积
    "bridge": ("kinect_direct", "python.exe",
               [PY, "-u", os.path.join(PROJECT, "kinect_direct.py")], PROJECT),
    "tower": ("tower_ui\\server.py", "python.exe",
              [PY, "-u", os.path.join(TOWER_UI, "server.py")], TOWER_UI),
    "yolo": ("kinect_yolo3d", "python.exe",
             [PY, "-u", os.path.join(PROJECT, "kinect_yolo3d.py"), "--interval", "2"], PROJECT),
    "vtuber": ("run_server.py", "python.exe",
               [UV, "run", "run_server.py"], VTUBER),
}
INTERVAL = 15
LOG = os.path.join(PROJECT, "keeper.log")
FRAME_FILE = os.path.join(TOWER_UI, "kinect_runtime", "color.jpg")
FRAME_STALE_SEC = 90  # 帧超过 90 秒未更新 → 判定 Kinect 设备僵死
# ---------------------------------------------------------------- 重启节流（重要）
# 本机 Kinect / 鼠标 / 键盘 / 摄像头 / 蓝牙全部挂在同一个 Intel xHCI 控制器下，
# bridge 每次重启都会让 Kinect 重新枚举 USB，把同一总线的键鼠一起带走。
# 而 bridge 写 color.jpg 用的是 GDI+ 直写文件路径，高频下会抛
# "GDI+ 一般性错误 / 文件被占用"，偶发丢失几帧很正常 —— 不该因此就重开硬件。
# 策略：①先软恢复（只重启识别器，不碰 Kinect）②真要动 bridge 时加冷却。
MIN_BRIDGE_REBOOT_INTERVAL = 15 * 60   # 两次 bridge 重启的最小间隔（秒）
SOFT_RECOVERY_ROUNDS = 2               # 先尝试软恢复的轮数
_last_bridge_reboot = 0.0
_freeze_rounds = 0

KINECT_RUNTIME_DIR = os.path.join(TOWER_UI, "kinect_runtime")
# 塔台 /api/kinect/power 关闭时写入此标志：keeper 不再拉起/自愈 Kinect 链路
KINECT_OFF_FLAG = os.path.join(TOWER_UI, "kinect_runtime", "kinect_off.flag")

_creationflags = 0x08000000  # CREATE_NO_WINDOW


def log(msg):
    line = time.strftime("%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def alive(name):
    """检查某服务是否有存活进程（按命令行关键词匹配）"""
    kw, pname, _, _ = SERVICES[name]
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if p.info["name"] and pname.lower() not in p.info["name"].lower():
                continue
            cl = " ".join(p.info["cmdline"] or [])
            if kw.lower() in cl.lower():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def start(name):
    _, _, args, cwd = SERVICES[name]
    # stdout/stderr 统一追加到各自日志，keeper 拉起的进程不再是"黑盒"
    logf = open(os.path.join(PROJECT, f"{name}_svc.log"), "a",
                encoding="utf-8", errors="replace")
    try:
        subprocess.Popen(args, cwd=cwd, creationflags=_creationflags,
                         stdout=logf, stderr=subprocess.STDOUT)
    finally:
        logf.close()
    log(f"[keeper] 已拉起 {name}")


def kill(name):
    """杀掉某服务的所有进程（用于僵死自愈）"""
    kw, pname, _, _ = SERVICES[name]
    n = 0
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if p.info["name"] and pname.lower() not in p.info["name"].lower():
                continue
            cl = " ".join(p.info["cmdline"] or [])
            if kw.lower() in cl.lower():
                p.kill()
                n += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return n


def frame_stale():
    """color.jpg 超过阈值秒数未更新 → Kinect 设备僵死（进程活着但不出帧）"""
    try:
        return (time.time() - os.path.getmtime(FRAME_FILE)) > FRAME_STALE_SEC
    except OSError:
        return True


def bridge_uptime():
    """采集进程已运行时长（秒）。Python 直连版按命令行匹配 kinect_direct。"""
    for p in psutil.process_iter(["name", "cmdline", "create_time"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
            if "kinect_direct" in cl.lower():
                return time.time() - p.info["create_time"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return 0


def _kinect_disabled():
    return os.path.exists(KINECT_OFF_FLAG)


def cleanup_replace_temp():
    """清理 File.Replace 失败留在 kinect_runtime 里的 ~RF*.TMP 残留。

    这是整条故障链的起点（实测确认）：bridge 每帧用 ReplaceFile 原子替换
    color.jpg/depth.jpg，偶尔失败就会在目录里留下 ~RF<hex>.TMP 垃圾。
    这些残留越堆越多会让 NTFS 目录操作急剧劣化 —— 实测出帧从 13 fps
    一路掉到 0.4 fps，color.jpg 彻底冻结，keeper 判定僵死后重启 Kinect，
    而重枚举 USB 会把同一控制器上的鼠标键盘一起带走。
    清理掉这些残留，帧率立刻回到 13 fps、写错误归零。

    只删 5 秒前生成的，避开 bridge 正在操作的那个。
    """
    try:
        names = os.listdir(KINECT_RUNTIME_DIR)
    except OSError:
        return 0
    removed = 0
    for name in names:
        if "~RF" in name and name.upper().endswith(".TMP"):
            path = os.path.join(KINECT_RUNTIME_DIR, name)
            try:
                if time.time() - os.path.getmtime(path) > 5:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass  # 正被使用，下轮再试
    if removed:
        log(f"[keeper] 清理 File.Replace 残留 {removed} 个 ~RF*.TMP"
            f"（不清理会把出帧从 13fps 拖到 0.4fps）")
    return removed


def ensure_running():
    started = []
    skip = {"bridge", "yolo"} if _kinect_disabled() else set()
    cleanup_replace_temp()   # 每轮兜底清理，防止残留堆积
    for name in SERVICES:
        if name in skip:
            continue
        try:
            if not alive(name):
                start(name)
                started.append(name)
        except Exception as e:
            log(f"[keeper] {name} 检查/启动失败: {e}")

    # 帧冻结自愈：仅当 Kinect 未被人为关闭时执行
    global _last_bridge_reboot, _freeze_rounds
    if not _kinect_disabled():
        try:
            if not frame_stale():
                _freeze_rounds = 0      # 画面已恢复流动，重置计数
            elif (not started and alive("bridge") and alive("yolo")
                    and bridge_uptime() > 60):
                _freeze_rounds += 1
                cool = time.time() - _last_bridge_reboot
                if _freeze_rounds == 1:
                    # 第一轮：多半只是被替换残留拖慢了，清理后常常直接自愈
                    cleanup_replace_temp()
                    log(f"[keeper] 帧冻结>{FRAME_STALE_SEC}s → 先清理替换残留并观察一轮，"
                        f"不重开 Kinect（保护同总线的鼠标键盘）")
                elif _freeze_rounds <= 1 + SOFT_RECOVERY_ROUNDS:
                    # 软恢复：只重启识别器，绝不重开 Kinect 硬件
                    log(f"[keeper] 帧冻结>{FRAME_STALE_SEC}s（第 {_freeze_rounds} 轮）"
                        f"→ 软恢复：只重启 yolo，不重开 Kinect（保住 USB 上的键鼠）")
                    kill("yolo")
                    time.sleep(1)
                    start("yolo")
                    started += ["yolo(soft-recovery)"]
                elif cool < MIN_BRIDGE_REBOOT_INTERVAL:
                    wait = int(MIN_BRIDGE_REBOOT_INTERVAL - cool)
                    log(f"[keeper] 帧冻结仍在（第 {_freeze_rounds} 轮），但距上次 bridge 重启仅 "
                        f"{int(cool)}s < {MIN_BRIDGE_REBOOT_INTERVAL}s → 再等 {wait}s。"
                        f"频繁重开 Kinect 会让同一控制器的鼠标键盘一起掉线。")
                else:
                    log(f"[keeper] 帧冻结>{FRAME_STALE_SEC}s，软恢复 {SOFT_RECOVERY_ROUNDS} 轮无效 "
                        f"→ 重启 bridge/yolo 重开设备")
                    kill("yolo")
                    kill("bridge")
                    time.sleep(2)
                    start("bridge")
                    start("yolo")
                    _last_bridge_reboot = time.time()
                    _freeze_rounds = 0
                    started += ["bridge(freeze)", "yolo(freeze)"]
        except Exception as e:
            log(f"[keeper] 帧冻结自愈失败: {e}")

    return {"ok": True, "started": started,
            "all": {n: alive(n) for n in SERVICES}}


def keeper_loop():
    while True:
        try:
            r = ensure_running()
            if r["started"]:
                log(f"[keeper] 本轮补拉: {r['started']}")
        except Exception as e:
            log(f"[keeper] 循环异常: {e}")
        time.sleep(INTERVAL)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers(); self.wfile.write(raw)

    def do_OPTIONS(self): self.send_json({"ok": True})
    def do_GET(self):
        if self.path == "/health": self.send_json(ensure_running())
        else: self.send_json({"ok": False, "msg": "not found"}, 404)
    def do_POST(self):
        if self.path == "/restart": self.send_json(ensure_running())
        else: self.send_json({"ok": False, "msg": "not found"}, 404)
    def log_message(self, *_): pass


if __name__ == "__main__":
    log("[keeper] 守护启动（每 %d 秒巡检: %s）" % (INTERVAL, ", ".join(SERVICES)))
    threading.Thread(target=keeper_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8091), Handler).serve_forever()
