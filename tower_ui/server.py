#!/usr/bin/env python3
"""塔台可视化界面 - 后端 API
在香橙派上运行，提供状态数据给前端页面。

v0.3 新增：
  - Tello 视频链路：ffmpeg 读 UDP 11111 H.264 → MJPEG → 浏览器 <img> 直接显示
  - 无人机在线探测（wlan0 是否在 192.168.10.x 网段 + battery? 查询）
"""

import platform
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from flask import Flask, Response, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="web", static_url_path="")

KINECT_RUNTIME_DIR = os.path.join(os.path.dirname(__file__), "kinect_runtime")
KINECT_STATE_PATH = os.path.join(KINECT_RUNTIME_DIR, "status.json")
KINECT_SCAN_CONTROL = os.path.join(KINECT_RUNTIME_DIR, "scan_control.json")
KINECT_SCAN_ROOT = os.path.join(KINECT_RUNTIME_DIR, "scans")
KINECT_STALE_AFTER = 3.0
# Kinect 总开关：此文件存在 = 用户主动关闭视觉链路，keeper 不再自动拉起
KINECT_OFF_FLAG = os.path.join(KINECT_RUNTIME_DIR, "kinect_off.flag")


@app.after_request
def add_cors_headers(response):
    """Allow the local preview page to control the Orange Pi tower."""
    response.headers.setdefault("Access-Control-Allow-Origin", "*")
    response.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
    response.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")
    response.headers.setdefault("Access-Control-Allow-Private-Network", "true")
    return response

# ---------------------------------------------------------------- Tello 参数
# 目标地址可配（tower_config.json 或环境变量 TELLO_IP 覆盖）。
# 默认指真实无人机的热点网关；跑模拟器时改指 127.0.0.1 即可，
# 塔台其余代码一行都不用动。
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tower_config.json")
TELLO_CMD_PORT = 8889
VIDEO_PORT = 11111


def load_config():
    cfg = {"tello_ip": "192.168.10.1", "tello_ssid": "TELLO-5896C7"}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    if os.environ.get("TELLO_IP"):
        cfg["tello_ip"] = os.environ["TELLO_IP"]
    return cfg


CONFIG = load_config()
TELLO_IP = CONFIG["tello_ip"]
TELLO_SSID = CONFIG.get("tello_ssid", "TELLO-5896C7")

def locate_ffmpeg():
    """找到可用的 ffmpeg 可执行文件（塔台视频转码全靠它）。

    踩过的坑：这台机器的 ffmpeg **不在 PATH**（只在一个无关项目的 venv 里躺着），
    而原来命令写死 "ffmpeg"，subprocess 抛 FileNotFoundError 被上层吞掉 ——
    现象就是点了"开启画面"永远没反应，日志里却看不到半点错误。
    所以这里按顺序探测：环境变量 → PATH → Python 自带的 imageio-ffmpeg → 常见安装位。
    """
    cands = []
    env = os.environ.get("FFMPEG")
    if env:
        cands.append(env)
    which = shutil.which("ffmpeg")
    if which:
        cands.append(which)
    try:
        import imageio_ffmpeg
        cands.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    # 兜底：按版本号扫 imageio-ffmpeg 的二进制目录
    sp = os.path.join(os.path.dirname(sys.executable), "Lib", "site-packages",
                      "imageio_ffmpeg", "binaries")
    if os.path.isdir(sp):
        for name in sorted(os.listdir(sp), reverse=True):
            if name.startswith("ffmpeg-win") and name.endswith(".exe"):
                cands.append(os.path.join(sp, name))
    winget = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                          "Microsoft", "WinGet", "Packages")
    if os.path.isdir(winget):
        for pkg in os.listdir(winget):
            if "ffmpeg" in pkg.lower():
                for root, _dirs, files in os.walk(os.path.join(winget, pkg)):
                    for f in files:
                        if f.lower() == "ffmpeg.exe":
                            cands.append(os.path.join(root, f))
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


FFMPEG = locate_ffmpeg()


def build_ffmpeg_cmd(port=11111):
    """构造 ffmpeg 转码命令：Tello 的裸 H.264 UDP 流 → MJPEG（浏览器能直接显示）"""
    exe = FFMPEG or locate_ffmpeg() or "ffmpeg"
    return [
        exe, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-f", "h264",
        "-i", f"udp://0.0.0.0:{port}?overrun_nonfatal=1&fifo_size=5000000",
        "-an", "-vf", "fps=20,scale=960:-2",
        "-f", "mpjpeg", "-boundary_tag", "boundary", "-q:v", "6",
        "pipe:1",
    ]


def get_local_ip():
    """获取局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def read_cpu_temp():
    """读取 CPU 温度（RK3588）"""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None


def get_mem_info():
    """读取内存使用（香橙派 5B 总共 4GB）"""
    try:
        out = subprocess.check_output(["free", "-m"]).decode()
        lines = out.strip().split("\n")
        parts = lines[1].split()
        total, used = int(parts[1]), int(parts[2])
        return {"total_mb": total, "used_mb": used, "percent": round(used / total * 100, 1)}
    except Exception:
        return None


def get_disk_info():
    """读取根分区磁盘使用"""
    try:
        out = subprocess.check_output(["df", "-B", "M", "/"]).decode()
        parts = out.strip().split("\n")[1].split()
        total, used = int(parts[1].rstrip("M")), int(parts[2].rstrip("M"))
        return {"total_mb": total, "used_mb": used, "percent": round(used / total * 100, 1)}
    except Exception:
        return None


def get_uptime():
    try:
        with open("/proc/uptime") as f:
            secs = int(float(f.read().split()[0]))
        h, m = secs // 3600, (secs % 3600) // 60
        return f"{h}小时{m}分"
    except Exception:
        return None


# ---------------------------------------------------------------- Tello 通信

def local_ipv4_list():
    """跨平台枚举本机所有 IPv4 地址（不含 127.0.0.1）。

    原实现只认 Linux 的 `ip addr show wlan0`，在 Windows 上恒为空，
    直接导致后面所有链路判定被跳过。这里按平台分别取值。
    """
    ips = set()
    if platform.system() == "Windows":
        # ipconfig 输出依赖系统代码页（简体中文是 GBK），用 errors=ignore 兜住
        for enc in ("gbk", "utf-8", "mbcs"):
            try:
                out = subprocess.check_output(
                    ["ipconfig"], timeout=5, stderr=subprocess.DEVNULL
                ).decode(enc, "ignore")
                break
            except Exception:
                out = ""
        ips |= set(re.findall(r"IPv4[^\d:]{0,30}:\s*(\d+\.\d+\.\d+\.\d+)", out))
    else:
        try:
            out = subprocess.check_output(
                ["ip", "-4", "-o", "addr"], text=True, timeout=3
            )
            ips |= set(re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", out))
        except Exception:
            pass
    # 兜底：主机名解析（Windows 上通常能列出全部网卡地址）
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    ips.discard("127.0.0.1")
    return ips


def tello_link_ip():
    """本机是否有落在 Tello 网段(192.168.10.x)的地址。

    有 = 已经连上 Tello AP 且拿到了真地址；
    只有 169.254.x.x 说明连上了 AP 但 DHCP 没给地址（Windows 常见），通信照样不通。
    """
    for ip in local_ipv4_list():
        if ip.startswith("192.168.10."):
            return ip
    return None


def wlan_apipa_ip():
    """本机是否拿到 169.254.x.x（APIPA 假地址）——用于诊断"连了热点但拿不到地址"。

    返回第一个 APIPA 地址，没有则 None。
    """
    for ip in local_ipv4_list():
        if ip.startswith("169.254."):
            return ip
    return None


def wlan_ip():
    """本机 Tello 链路地址（跨平台）。保留旧名以兼容既有调用。"""
    return tello_link_ip()


def wifi_signal():
    """Tello 热点信号强度 dBm（跨平台）。"""
    try:
        if platform.system() == "Windows":
            raw = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"], timeout=5,
                stderr=subprocess.DEVNULL
            ).decode("gbk", "ignore")
            m = re.search(r"信号\s*:\s*(\d+)%", raw) or re.search(r"Signal\s*:\s*(\d+)%", raw)
            if m:
                # Windows 只给百分比，粗略映射到 dBm 区间（-100 ~ -30）
                pct = int(m.group(1))
                return int(-100 + (pct / 100.0) * 70)
            return None
        out = subprocess.check_output(["iw", "dev", "wlan0", "link"], text=True, timeout=3)
        m = re.search(r"signal:\s*(-?\d+)\s*dBm", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None

def new_udp_socket():
    """创建一个 UDP socket，并顺手关掉 Windows 的 WSAECONNRESET 行为。

    Windows 特有坑：UDP socket 收到 ICMP port-unreachable 之后，后续每一次
    recvfrom 都会抛 ConnectionResetError —— 探测一台不在线的 Tello 就会触发，
    表现为"明明连着却一直报无响应/离线"。关掉这个行为后，超时就是纯超时。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if platform.system() == "Windows" and hasattr(socket, "SIO_UDP_CONNRESET"):
        try:
            s.ioctl(socket.SIO_UDP_CONNRESET, False)
        except Exception:
            pass
    return s


# 单一持久命令出口：所有 fire-and-forget 发包（rc 摇杆 / 心跳 / 探测握手）
# 复用同一个 socket，源端口固定 8889。
# 旧写法"每条指令新建一个 socket 再立刻 close"是命令通道卡死的主因：
# Tello 的 'ok' 应答晚到几毫秒，落在已关闭的端口上 → ICMP 不可达 →
# Windows 下还叠加 WSAECONNRESET；巡逻时 rc 是 10Hz，每秒建/关 10 个 socket，
# 临时端口被迅速耗尽，命令通道随即冻结。
_MAIN_SOCK = None
_main_sock_lock = threading.Lock()


def main_sock():
    global _MAIN_SOCK
    if _MAIN_SOCK is None:
        s = new_udp_socket()
        try:
            s.bind(("", TELLO_CMD_PORT))
        except OSError:
            s.bind(("", 0))  # 8889 被占用时退让，仍复用这一个 socket
        _MAIN_SOCK = s
    return _MAIN_SOCK


def send_fast(text):
    """fire-and-forget：用共享 socket 发一条，不等回复、不关 socket。"""
    with _main_sock_lock:
        main_sock().sendto(text.encode(), (TELLO_IP, TELLO_CMD_PORT))


def tello_cmd(cmd, timeout=5.0, retries=2):
    """向 Tello 发送 SDK 命令并等待文本回复。

    关键（实测定性）：Tello 只把 'ok' / 电量等应答发回 **源端口 8889**，
    不遵循标准 UDP 的"回源端口"行为 —— 用随机端口发（bind(("",0))）
    18 秒零回包，会被误判成"命令通道冻结 / 离线"。这正是此前一直连不上的根因。
    因此这里必须复用绑定了 8889 的共享 socket（main_sock）。

    发送前后各清一次残留应答，避免把上一条（例如 rc 的 ok）当成本次回复。
    """
    last = None
    s = main_sock()
    for _ in range(retries):
        try:
            # 先排空缓冲区里可能残留的旧应答
            s.setblocking(False)
            while True:
                try:
                    s.recvfrom(2048)
                except OSError:
                    break
            s.settimeout(timeout)
            send_fast(cmd)
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    data, _ = s.recvfrom(2048)
                except socket.timeout:
                    break
                text = data.decode("ascii", "ignore").strip()
                if text:  # 非 ASCII 的 state 二进制包解码后为空，跳过
                    return text
            last = f"等待 {cmd!r} 回复超时"
        except Exception as e:
            last = e
    raise RuntimeError(f"tello 命令 {cmd!r} 无响应: {last}")


_sdk = {"ok_until": 0.0}


def ensure_sdk():
    """Tello 每次上电后必须先发一次 command 才能进入 SDK 模式。"""
    if time.time() < _sdk["ok_until"]:
        return
    resp = tello_cmd("command", timeout=5.0, retries=3)
    if "ok" in resp.lower():
        _sdk["ok_until"] = time.time() + 300
    else:
        raise RuntimeError(f"command 握手失败: {resp}")


# 无人机探测缓存（后台线程每 5 秒刷新，避免拖慢状态轮询）
PROBE = {"ts": 0.0, "online": False, "battery": None, "wlan": None,
         "apipa": None, "hint": None}
_probe_lock = threading.Lock()


# ------------------------------------------------------------- 无人机温度监控
# Tello 的 8890 端口持续广播状态（含机身温度）。实测：视频编码 + 高温会把
# 命令通道（8889）压到完全无响应（连 battery? 都不回），且不会自愈，
# 只能断电散热。因此把温度读出来，作为"能不能开视频 / 能不能发 rc"的闸门。

TELLO_STATE = {"ts": 0.0, "ok": False, "temph": None, "templ": None, "bat": None}
_state_lock = threading.Lock()
TELLO_TEMP_WARN = 82.0  # 达到此温度禁止开视频 / 禁止起飞
TELLO_TEMP_HARD = 88.0  # 达到此温度强制悬停


def tello_temp():
    """机身当前温度（°C）；状态广播中断超过 5 秒则返回 None。"""
    with _state_lock:
        if TELLO_STATE["ok"] and (time.time() - TELLO_STATE["ts"]) < 5.0:
            return TELLO_STATE["temph"]
    return None


def _state_loop():
    """监听 8890 状态广播，刷新温度/电量。"""
    while True:
        s = new_udp_socket()
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", 8890))
            s.settimeout(2.0)
        except OSError:
            s.close()
            time.sleep(3)
            continue
        while True:
            try:
                data, _ = s.recvfrom(1024)
            except socket.timeout:
                with _state_lock:
                    TELLO_STATE["ok"] = False
                continue
            except OSError:
                break
            text = data.decode("ascii", "ignore").strip()
            if "temph" not in text:
                continue
            f = dict(x.split(":", 1) for x in text.split(";") if ":" in x)

            def _num(key):
                try:
                    return float(f.get(key))
                except (TypeError, ValueError):
                    return None

            with _state_lock:
                TELLO_STATE.update(ts=time.time(), ok=True,
                                   temph=_num("temph"), templ=_num("templ"),
                                   bat=_num("bat"))
        s.close()
        time.sleep(2)


# ------------------------------------------------------- 视觉位姿（高频，闭环用）
# kinect_yolo3d 每帧经 UDP 推来真实 Tello 位姿。相比 /api/vision/detections 的
# 低频 HTTP 上报，这条通道 5fps 且不含回灌的幽灵坐标，专门喂给悬停/巡逻控制器。

POSE_PORT = int(os.environ.get("POSE_PORT", "8898"))
VISION_POSE = {"ts": 0.0, "x": None, "y": None, "z": None,
               "conf": None, "label": None, "src": None, "hb_ts": 0.0}
_pose_lock = threading.Lock()


def _pose_num(v):
    """UDP 字段 → 有限浮点；缺失 / 非数字 / NaN 都不是有效位姿。"""
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f == float("inf") or f == float("-inf"):
        return None
    return f


def _apply_vision_pose(p, now=None):
    """有效 x 与 z 才刷新 pose ts。纯心跳只记 hb_ts，pose_age 从上一帧真实位姿继续涨。"""
    now = time.time() if now is None else now
    x = _pose_num(p.get("x") if isinstance(p, dict) else None)
    z = _pose_num(p.get("z") if isinstance(p, dict) else None)
    with _pose_lock:
        VISION_POSE["hb_ts"] = now
        if x is None or z is None:
            VISION_POSE["x"] = None
            VISION_POSE["y"] = None
            VISION_POSE["z"] = None
            VISION_POSE["conf"] = None
            VISION_POSE["label"] = None
            src = p.get("src") if isinstance(p, dict) else None
            if src:
                VISION_POSE["src"] = src
            return False
        VISION_POSE.update(
            ts=now, x=x, y=_pose_num(p.get("y")), z=z,
            conf=p.get("conf"), label=p.get("label"), src=p.get("src"),
        )
        return True


def vision_pose(max_age=0.6):
    """最新真实 Tello 位姿；过期或本帧无目标时返回 None（控制器据此悬停）。"""
    with _pose_lock:
        if (time.time() - VISION_POSE["ts"]) < max_age and VISION_POSE["x"] is not None:
            return dict(VISION_POSE)
    return None


def _pose_loop():
    """监听 kinect_yolo3d 的 UDP 位姿推送。"""
    s = new_udp_socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", POSE_PORT))
    except OSError as e:
        print(f"[pose] 绑定 {POSE_PORT} 失败，位姿闭环不可用: {e}")
        return
    while True:
        try:
            data, _ = s.recvfrom(2048)
        except OSError:
            time.sleep(1)
            continue
        try:
            p = json.loads(data.decode("utf-8", "ignore"))
        except ValueError:
            continue
        _apply_vision_pose(p)


# ------------------------------------------------------- rc 指令 UDP 入口
# 悬停/巡逻控制器以 10Hz 下发 rc。若走 HTTP POST，每秒 10 个 TCP 连接会堆
# TIME_WAIT（历史上 12fps 时就因端口耗尽拖垮过 USB）。改走 UDP：控制器只管发，
# 塔台收下后用 8889 的共享 socket 转出去 —— 零连接、零残留、延迟最低。

RC_PORT = int(os.environ.get("RC_PORT", "8897"))


def _rc_axis(v):
    try:
        return int(max(-100, min(100, float(v))))
    except (TypeError, ValueError):
        return 0


def _rc_udp_loop():
    """接收控制器的 UDP rc，做温控闸门后转发给 Tello。"""
    s = new_udp_socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", RC_PORT))
    except OSError as e:
        print(f"[rc] 绑定 {RC_PORT} 失败，UDP rc 通道不可用: {e}")
        return
    while True:
        try:
            data, _ = s.recvfrom(1024)
        except OSError:
            time.sleep(1)
            continue
        try:
            p = json.loads(data.decode("utf-8", "ignore"))
        except ValueError:
            continue
        a = _rc_axis(p.get("a"))
        b = _rc_axis(p.get("b"))
        c = _rc_axis(p.get("c"))
        d = _rc_axis(p.get("d"))
        t = tello_temp()
        if t is not None and t >= TELLO_TEMP_HARD:
            try:
                send_fast("rc 0 0 0 0")
            except Exception:
                pass
            _RC["active"] = False
            continue
        try:
            send_fast(f"rc {a} {b} {c} {d}")
            _RC["active"] = True
            _RC["last"] = time.time()
        except Exception:
            pass


def video_packets_flowing(wait=4.0, min_bytes=20000):
    """监听 VIDEO_PORT 判断视频流是否在发数据。

    实测发现 Tello 的 streamon 经常不回 'ok'（命令通道冻结），
    但视频包照发不误——所以以端口数据为准，不信回执。
    端口被 ffmpeg 占用时视为"流在跑"。
    """
    s = new_udp_socket()
    try:
        s.settimeout(wait)
        s.bind(("", VIDEO_PORT))
    except OSError:
        return min_bytes  # 端口已被占用 = ffmpeg 正在收流
    total = 0
    t0 = time.time()
    try:
        while time.time() - t0 < wait:
            try:
                data, _ = s.recvfrom(2048)
            except socket.timeout:
                break
            total += len(data)
            if total >= min_bytes:
                break
    finally:
        s.close()
    return total


def _probe_loop():
    """后台探测 Tello 是否在线。

    关键修正：原逻辑必须先靠 wlan_ip() 拿到 192.168.10.x 才肯握手，
    而该函数在 Windows 上恒为 None，等于整段跳过。现在改成
    「能握手就算在线」，本机 IP 只作为诊断信息。
    """
    while True:
        ip = tello_link_ip()
        apipa = wlan_apipa_ip()
        online = False
        hint = None

        if video_status()["running"]:
            online = True  # ffmpeg 在跑 = 显然在线
        elif ip:
            # 已连上 Tello 网段即视为在线；握手成功顺便刷新电量。
            # 注意：旧写法是「只有握手失败才置在线」，握手成功时反而显示离线 —— 已修正。
            online = True
            if not _probe_once():
                hint = "Tello 网段已连通，但命令握手失败（命令口可能异常）"
        else:
            # 没有 Tello 网段地址时仍试一次握手——有些环境枚举不到 IP 但实际可达
            online = _probe_once()
            if not online:
                if apipa:
                    hint = ("连上了热点但没拿到 IP（169.254.x.x），"
                            "需要给无线网卡设静态地址 192.168.10.2")
                else:
                    hint = "未连接 Tello 热点（192.168.10.x 网段无本机地址）"

        with _probe_lock:
            # 不要在这里回写 battery：_probe_once() 刚更新过，
            # 用循环开头读到的旧值覆盖会把电量永远钉死在初始的 None。
            PROBE.update(ts=time.time(), online=online,
                         wlan=ip, apipa=apipa, hint=hint)
        # 没连上的时候放慢轮询，别让后台线程空转抢资源
        time.sleep(5 if (ip or online) else 10)


def _probe_once():
    """试一次 command 握手 + 读电量。成功 True，失败 False。"""
    try:
        ensure_sdk()
        # 重试读到数字为止：keepalive 的 'command' 应答偶尔会串进等待窗口，
        # 命中时只会读到 'ok'（不含数字），重试一次即可拿到真实电量。
        for _ in range(3):
            raw = tello_cmd("battery?", timeout=2.0, retries=1)
            m = re.search(r"(\d{1,3})", raw)
            if m:
                with _probe_lock:
                    PROBE["battery"] = max(0, min(100, int(m.group(1))))
                break
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 视频链路

VIDEO = {"proc": None, "started_at": None, "error": None, "subscribers": set(), "lock": threading.Lock()}


def video_status():
    proc = VIDEO["proc"]
    running = bool(proc and proc.poll() is None)
    return {
        "running": running,
        "started_at": VIDEO["started_at"] if running else None,
        "error": VIDEO["error"],
    }


def video_start():
    """streamon + 启动 ffmpeg 转码。

    Tello 的 streamon 回执不可靠（实测经常不回 'ok' 但流照发），
    因此以「视频端口收到数据包」为成功判据。
    """
    p = VIDEO["proc"]
    if p and p.poll() is None:
        return {"ok": True, "msg": "视频已在运行"}

    t = tello_temp()
    if t is not None and t >= TELLO_TEMP_WARN:
        return {"ok": False,
                "msg": f"无人机温度 {t:.0f}°C 已达 {TELLO_TEMP_WARN:.0f}°C 上限，"
                       f"禁止开视频流（视频编码是命令通道冻结/过热关机的主因）。"
                       f"请断电让机身前置通风散热后再试。"}

    if not FFMPEG:
        return {"ok": False,
                "msg": "找不到 ffmpeg，无法转码视频。请安装 ffmpeg，或设置环境变量 "
                       "FFMPEG 指向 ffmpeg.exe（本机 ffmpeg 不在 PATH，这是之前画面出不来的主因）"}

    # 本机 IP 网段只作为提示，不作为前置门槛：
    # 有些环境枚举不到 IP 但链路实际是通的，拿它当判据会把可用链路直接判死。
    note = ""
    if not tello_link_ip():
        apipa = wlan_apipa_ip()
        if apipa:
            note = f"（提示：无线网卡只有 {apipa}，建议设静态 192.168.10.2）"
        else:
            note = "（提示：本机尚无 Tello 网段地址，若失败请先连上 TELLO-* 热点）"

    # 尽力握手 + 发 streamon（命令通道可能冻结，失败不致命）
    try:
        ensure_sdk()
    except Exception:
        pass
    try:
        tello_cmd("streamon", timeout=3.0, retries=1)
    except Exception:
        pass

    # 用数据包验证：先看是否已在流（之前发过的 streamon 可能已生效）
    got = video_packets_flowing(wait=4.0, min_bytes=20000)
    if got < 20000:
        # 没流 → 再补一发 streamon，再等一轮
        try:
            tello_cmd("streamon", timeout=3.0, retries=1)
        except Exception:
            pass
        got = video_packets_flowing(wait=4.0, min_bytes=20000)
    if got < 20000:
        apipa_msg = ""
        if not tello_link_ip():
            apipa = wlan_apipa_ip()
            if apipa:
                apipa_msg = f"｜无线网卡只有假地址 {apipa}，需设静态 192.168.10.2"
            else:
                apipa_msg = "｜本机未连接 Tello 热点"
        return {"ok": False,
                "msg": f"已发送 streamon 但未收到视频数据包{apipa_msg}"
                       f"（无人机可能未开机/电量过低/命令通道冻结，建议充电后断电重启）"}

    try:
        proc = subprocess.Popen(build_ffmpeg_cmd(), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
    except FileNotFoundError:
        FFMPEG_LOCAL = locate_ffmpeg()
        return {"ok": False,
                "msg": f"ffmpeg 启动失败，路径不存在：{FFMPEG_LOCAL}（设置 FFMPEG 环境变量重试）"}
    VIDEO["proc"] = proc
    VIDEO["started_at"] = time.time()
    VIDEO["last_data_ts"] = time.time()
    VIDEO["error"] = None

    def _watch():
        err = b""
        try:
            err = proc.stderr.read()
        except Exception:
            pass
        code = proc.wait()
        if code != 0:
            VIDEO["error"] = (err.decode(errors="replace") or f"ffmpeg 退出码 {code}")[-400:]

    threading.Thread(target=_watch, daemon=True).start()
    base = f"视频流已确认（{got // 1024}KB 采样）· 转码已启动"
    return {"ok": True, "msg": base + note}


def video_stop():
    proc = VIDEO["proc"]
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
    VIDEO["proc"] = None
    try:
        tello_cmd("streamoff", timeout=2.0, retries=1)
    except Exception:
        pass
    return {"ok": True}


def _keepalive_loop():
    """每 45 秒向 Tello 发一条 command 心跳（fire-and-forget）。

    Tello 在长时间无 SDK 指令时会自动关机（即使正在传视频），
    定期心跳可以阻止这个行为。只要探测认为在线就发（不再依赖本机 IP 判定，
    因为在 Windows 上那种判定会失效导致心跳根本不发，Tello 照样自己关机）。
    """
    while True:
        with _probe_lock:
            online = PROBE.get("online")
        if online or video_status()["running"]:
            try:
                send_fast("command")
            except Exception:
                pass
        time.sleep(20)


def _video_watchdog_loop():
    """视频看门狗 + 摇杆 deadman。"""
    while True:
        time.sleep(5)
        # 视频看门狗：ffmpeg 运行中若 20 秒无新数据，判定无人机断流，
        # 主动杀掉转码进程让前端立刻感知（否则 <img> 会僵死在最后一帧）。
        proc = VIDEO["proc"]
        if proc and proc.poll() is None:
            last = VIDEO.get("last_data_ts") or VIDEO.get("started_at") or time.time()
            if time.time() - last > 20:
                VIDEO["error"] = "视频流超过 20 秒无数据（无人机可能已关机或失联）"
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
                VIDEO["proc"] = None
        # 摇杆 deadman：0.5 秒无新 rc 指令 → 自动补一条悬停
        if _RC["active"] and time.time() - _RC["last"] > 0.5:
            _RC["active"] = False
            try:
                _raw_send("rc 0 0 0 0")
            except Exception:
                pass


@app.route("/video/mjpg")
def video_mjpg():
    """浏览器 <img> 直接消费的 MJPEG 流"""
    proc = VIDEO["proc"]
    if not proc or proc.poll() is not None:
        return Response("video not running", status=503)

    def gen():
        stream = proc.stdout
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                VIDEO["last_data_ts"] = time.time()
                yield chunk
        except Exception:
            pass

    return Response(
        gen(),
        mimetype="multipart/x-mixed-replace; boundary=boundary",
        headers={"Cache-Control": "no-store"},
    )


@app.route("/api/uav/video/start", methods=["POST"])
def api_video_start():
    return jsonify(video_start())


@app.route("/api/uav/video/stop", methods=["POST"])
def api_video_stop():
    return jsonify(video_stop())


# ---------------------------------------------------------------- 手动操控

_RC = {"active": False, "last": 0.0}
_FLIGHT_TEST = {"active": False, "started_at": None}

_UAV_CMD_ALLOWED = {
    "takeoff", "land", "emergency",
    "streamon", "streamoff", "command",
    "battery?", "wifi?", "speed?", "time?",
}


# ---------------------------------------------------------------- 视觉识别上报
# 识别器（笔记本 YOLO / 未来的橙派 NPU）作为独立进程，喝视频流、吐检测结果到这里

VISION = {"ts": 0.0, "source": None, "objects": [], "change": None, "stale_after": 8.0}


@app.route("/api/vision/detections", methods=["POST"])
def api_vision_detections():
    """接收识别器上报的检测结果（对象数组）

    对象可带相机坐标系三维坐标 x/y/z（米，来自 Kinect 深度反投影），
    没有深度的识别器只报 label/conf，x/y/z 为 null。
    另可带 change（变化度）：{changed, ratio, mean_mm, level}。
    """
    data = request.get_json(silent=True) or {}
    objs = data.get("objects") or []
    if not isinstance(objs, list):
        objs = []
    objs = objs[:20]  # 防滥用上限

    def _coord(o, key):
        try:
            value = o.get(key)
            return None if value is None else round(float(value), 3)
        except (TypeError, ValueError):
            return None

    raw_change = data.get("change")
    change = None
    if isinstance(raw_change, dict):
        def _num(value):
            try:
                return None if value is None else round(float(value), 4)
            except (TypeError, ValueError):
                return None
        change = {
            "changed": bool(raw_change.get("changed")),
            "ratio": _num(raw_change.get("ratio")),
            "mean_mm": _num(raw_change.get("mean_mm")),
            "level": str(raw_change.get("level", ""))[:8],
        }

    def _phys(value):
        """物理宽高（米）→ 长度 2 的浮点数组，异常给 None"""
        try:
            if value is None:
                return None
            return [round(float(value[0]), 3), round(float(value[1]), 3)]
        except (TypeError, ValueError, IndexError, KeyError):
            return None

    VISION["ts"] = time.time()
    VISION["source"] = str(data.get("source", "unknown"))[:40]
    VISION["change"] = change
    VISION["objects"] = [
        {
            "label": str(o.get("label", "?"))[:30],
            "conf": round(float(o.get("conf", 0)), 3),
            "x": _coord(o, "x"),
            "y": _coord(o, "y"),
            "z": _coord(o, "z"),
            # 无人机专项字段：是否无人机、是否通过尺寸校验、物理宽高（米）
            "drone": bool(o.get("drone")) if o.get("drone") is not None else None,
            "verified": bool(o.get("verified")) if o.get("verified") is not None else None,
            "phys_m": _phys(o.get("phys_m") or o.get("phys")),
            "held": bool(o.get("held")),
            "promoted_from": (str(o.get("promoted_from"))[:20] if o.get("promoted_from") else None),
        }
        for o in objs if isinstance(o, dict)
    ]
    return jsonify({"ok": True, "received": len(VISION["objects"])})


def vision_status():
    fresh = VISION["ts"] > 0 and (time.time() - VISION["ts"]) < VISION["stale_after"]
    return {
        "active": fresh,
        "source": VISION["source"] if fresh else None,
        "objects": VISION["objects"] if fresh else [],
        "change": VISION["change"] if fresh else None,
        "ts": VISION["ts"] if fresh else None,
        # 高频位姿（UDP 5fps）：控制器闭环专用，不含 hold 回灌的幽灵坐标。
        # pose_age 只看上一帧有效 x/z 的 ts；心跳见 pose_hb_age。
        "pose": vision_pose(2.0),
        "pose_age": (round(time.time() - VISION_POSE["ts"], 3) if VISION_POSE["ts"] else None),
        "pose_hb_age": (round(time.time() - VISION_POSE["hb_ts"], 3) if VISION_POSE.get("hb_ts") else None),
    }


def _raw_send(text):
    send_fast(text)


@app.route("/api/uav/rc", methods=["POST"])
def api_uav_rc():
    """虚拟摇杆速度指令（fire-and-forget，10Hz 调用，不等待回复）"""
    data = request.get_json(silent=True) or {}

    def _axis(v):
        try:
            return max(-100, min(100, int(v)))
        except (TypeError, ValueError):
            return 0

    a, b, c, d = (_axis(data.get(k, 0)) for k in ("a", "b", "c", "d"))
    t = tello_temp()
    if t is not None and t >= TELLO_TEMP_HARD:
        # 超硬红线：无视来令，强制悬停，防止继续加热直到命令通道冻结/关机
        try:
            _raw_send("rc 0 0 0 0")
        except Exception:
            pass
        _RC["active"] = False
        return jsonify({"ok": False, "temp": t,
                        "msg": f"温度 {t:.0f}°C 超硬红线 {TELLO_TEMP_HARD:.0f}°C，已强制悬停"})
    _RC["active"] = True
    _RC["last"] = time.time()
    try:
        _raw_send(f"rc {a} {b} {c} {d}")
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})
    return jsonify({"ok": True})


@app.route("/api/uav/cmd", methods=["POST"])
def api_uav_cmd():
    """动作指令（起飞/降落/急停等，白名单制）"""
    data = request.get_json(silent=True) or {}
    cmd = str(data.get("cmd", ""))
    if cmd not in _UAV_CMD_ALLOWED:
        return jsonify({"ok": False, "msg": f"不允许的命令: {cmd}"})
    # 起飞温控闸门：网页控制台和 tello_hover 都走这个口，此前只有 flight-test
    # 有闸门，等于留了个绕过的后门。
    if cmd == "takeoff":
        t = tello_temp()
        if t is not None and t >= TELLO_TEMP_WARN:
            return jsonify({"ok": False,
                            "msg": f"温度 {t:.0f}°C 已达 {TELLO_TEMP_WARN:.0f}°C 上限，"
                                   f"禁止起飞（过热起飞会导致命令通道冻结）。请冷却后再试。"}), 409
    try:
        resp = tello_cmd(cmd, timeout=4.0, retries=1)
        return jsonify({"ok": True, "msg": resp})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route("/api/uav/flight-test", methods=["POST"])
def api_uav_flight_test():
    """Controlled test: take off, wait one second, then land."""
    global _FLIGHT_TEST
    with _probe_lock:
        probe = dict(PROBE)
    if _FLIGHT_TEST["active"]:
        return jsonify({"ok": False, "msg": "试飞流程正在执行"}), 409
    if not probe["online"]:
        return jsonify({"ok": False, "msg": "Tello 未连接或 SDK 无响应"}), 409
    t = tello_temp()
    if t is not None and t >= TELLO_TEMP_WARN:
        return jsonify({"ok": False,
                        "msg": f"温度 {t:.0f}°C 过高，禁止起飞（过热起飞会导致命令通道冻结）"}), 409
    _FLIGHT_TEST = {"active": True, "started_at": time.time()}

    def run_test():
        global _FLIGHT_TEST
        try:
            ensure_sdk()
            takeoff = tello_cmd("takeoff", timeout=5.0, retries=1)
            time.sleep(3.0)
            land = tello_cmd("land", timeout=5.0, retries=1)
            _FLIGHT_TEST["result"] = {"takeoff": takeoff, "land": land}
        except Exception as exc:
            _FLIGHT_TEST["result"] = {"error": str(exc)}
            try:
                tello_cmd("land", timeout=5.0, retries=1)
            except Exception:
                pass
        finally:
            _FLIGHT_TEST["active"] = False

    threading.Thread(target=run_test, daemon=True).start()
    return jsonify({"ok": True, "msg": "已开始：起飞后 3 秒自动降落"})


# ------------------------------------------------ Kinect v2 状态

def kinect_status():
    """读取 Windows Kinect v2 桥接程序写入的最新状态。"""
    fallback = {
        "connected": False,
        "available": False,
        "frames_active": False,
        "sensor": "Kinect v2",
        "note": "等待 Kinect 桥接程序",
        "body_count": 0,
        "bodies": [],
        "color_url": "/kinect/color.jpg",
        "depth_url": "/kinect/depth.jpg",
    }
    try:
        with open(KINECT_STATE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return fallback
        updated = os.path.getmtime(KINECT_STATE_PATH)
        fresh = (time.time() - updated) <= KINECT_STALE_AFTER
        data = {**fallback, **data}
        data["connected"] = bool(data.get("connected") or data.get("ok")) and fresh
        data["available"] = bool(data.get("available") or data.get("ok")) and fresh
        data["frames_active"] = bool(data.get("frames_active") or data.get("running")) and fresh
        if data.get("frames") is not None: data["note"] = "RGB-D 帧流正常 · " + str(data.get("frames")) + " 帧"
        data["scan"] = kinect_scan_state()
        data["scan"] = kinect_scan_state()
        if not fresh:
            data["note"] = "Kinect 桥接数据已超时"
            data["body_count"] = 0
            data["bodies"] = []
        return data
    except (OSError, ValueError, TypeError):
        return fallback


def kinect_scan_state():
    try:
        with open(KINECT_SCAN_CONTROL, "r", encoding="utf-8") as handle:
            control = json.load(handle)
        directory = control.get("directory")
        frames = len([name for name in os.listdir(directory) if name.endswith("_color.jpg")]) if directory and os.path.isdir(directory) else 0
        return {"active": bool(control.get("active")), "directory": directory, "frames": frames}
    except (OSError, ValueError, TypeError):
        return {"active": False, "directory": None, "frames": 0}


@app.route("/api/kinect/scan/start", methods=["POST"])
def kinect_scan_start():
    """写 scan_control.json 让桥接侧开始落盘；依赖 Kinect 运行时目录可写。"""
    if os.path.exists(KINECT_OFF_FLAG):
        return jsonify({
            "ok": False,
            "msg": "Kinect 视觉已关闭，请先开启电源后再开始扫描",
            "error": "kinect_powered_off",
        }), 409
    state = kinect_scan_state()
    if state.get("active"):
        return jsonify({
            "ok": False,
            "msg": "卧室扫描已在进行中，请先结束当前扫描",
            "error": "already_scanning",
            "directory": state.get("directory"),
            "frames": state.get("frames", 0),
        }), 409
    try:
        os.makedirs(KINECT_SCAN_ROOT, exist_ok=True)
        stamp = time.strftime("bedroom_%Y%m%d_%H%M%S")
        directory = os.path.join(KINECT_SCAN_ROOT, stamp)
        os.makedirs(directory, exist_ok=True)
        with open(KINECT_SCAN_CONTROL, "w", encoding="utf-8") as handle:
            json.dump({"active": True, "directory": directory, "started_at": time.time()}, handle)
        return jsonify({"ok": True, "msg": "卧室扫描已开始", "directory": stamp})
    except OSError as e:
        return jsonify({
            "ok": False,
            "msg": "无法创建扫描目录或写入控制文件（磁盘/权限？）",
            "error": str(e),
        }), 500


@app.route("/api/kinect/scan/stop", methods=["POST"])
def kinect_scan_stop():
    """结束当前卧室扫描；若本来未在扫，返回明确提示。"""
    state = kinect_scan_state()
    if not state.get("active"):
        return jsonify({
            "ok": False,
            "msg": "当前没有进行中的卧室扫描",
            "error": "not_scanning",
            "frames": state.get("frames", 0),
            "directory": state.get("directory"),
        }), 409
    try:
        os.makedirs(KINECT_RUNTIME_DIR, exist_ok=True)
        with open(KINECT_SCAN_CONTROL, "w", encoding="utf-8") as handle:
            json.dump({"active": False, "directory": state.get("directory")}, handle)
        return jsonify({
            "ok": True,
            "msg": "卧室扫描已结束",
            "frames": state.get("frames", 0),
            "directory": state.get("directory"),
        })
    except OSError as e:
        return jsonify({
            "ok": False,
            "msg": "无法写入扫描控制文件",
            "error": str(e),
        }), 500


@app.route("/kinect/<path:filename>")
def kinect_asset(filename):
    """提供 Kinect 桥接程序生成的 RGB / 深度预览图。"""
    return send_from_directory(KINECT_RUNTIME_DIR, filename, max_age=0)


# ---------------------------------------------------------------- Kinect 画面转发
# 识别器（跑在接 Kinect 的那台机器上）把「已画好 YOLO 识别框」的画面推到这里，
# 塔台缓存最新一帧并用 MJPEG 吐给浏览器。
# 这样仪表盘能直接看到视频+识别框，且浏览器只需要连塔台一个源（跨机器问题由这里兜住）。
KINECT_FRAME = {"bytes": None, "ts": 0.0, "lock": threading.Lock()}
KINECT_FRAME_STALE_AFTER = 10.0

# 同机共享文件推流：识别器原子写 kinect_runtime/annotated.jpg，
# 塔台后台线程监视该文件并加载进 KINECT_FRAME（下游 MJPEG/状态逻辑不变）。
# 取代 12fps 的 HTTP POST 推流——那会堆积 1500+ TIME_WAIT，
# 耗尽端口并撑爆内核非分页池，导致全系统 USB 输入假死。
ANNOTATED_JPG = os.path.join(KINECT_RUNTIME_DIR, "annotated.jpg")


def _annotated_frame_loop():
    """每 40ms 检查 annotated.jpg；有新帧（mtime 变化）就读入内存"""
    last_mtime = 0.0
    while True:
        try:
            mt = os.path.getmtime(ANNOTATED_JPG)
            if mt > last_mtime:
                with open(ANNOTATED_JPG, "rb") as f:
                    data = f.read()
                if data:
                    with KINECT_FRAME["lock"]:
                        KINECT_FRAME["bytes"] = data
                        KINECT_FRAME["ts"] = time.time()
                last_mtime = mt
        except OSError:
            pass  # 文件还没出现 / 正被原子替换
        time.sleep(0.04)


def kinect_frame_status():
    with KINECT_FRAME["lock"]:
        data = KINECT_FRAME["bytes"]
        ts = KINECT_FRAME["ts"]
    fresh = data is not None and (time.time() - ts) < KINECT_FRAME_STALE_AFTER
    return {
        "available": fresh,
        "bytes": len(data) if data else 0,
        "age_s": round(time.time() - ts, 1) if ts else None,
    }


@app.route("/api/kinect/frame", methods=["POST"])
def api_kinect_frame():
    """接收识别器推送的画面（JPEG 原始字节，不带 multipart 包装）"""
    data = request.get_data()
    if not data:
        return jsonify({"ok": False, "msg": "空帧"}), 400
    if len(data) > 4 * 1024 * 1024:
        return jsonify({"ok": False, "msg": "帧过大（>4MB）"}), 400
    with KINECT_FRAME["lock"]:
        KINECT_FRAME["bytes"] = data
        KINECT_FRAME["ts"] = time.time()
    return jsonify({"ok": True, "bytes": len(data)})


@app.route("/kinect/live.mjpg")
def kinect_live_mjpg():
    """把最新一帧循环成 MJPEG，浏览器 <img> 直接当视频看"""
    def gen():
        while True:
            with KINECT_FRAME["lock"]:
                frame = KINECT_FRAME["bytes"]
            if frame:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                       + frame + b"\r\n")
            time.sleep(0.033)

    return Response(gen(),
                    mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"Cache-Control": "no-store"})

STATE = {
    "ugv": {"count": 0, "list": []},
    "missions": [],   # 任务日志
}
_MISSION_SEQ = 0

CAPABILITIES = [
    {"name": "外部视觉定位", "stage": "P0", "status": "pending", "desc": "Kinect v2 + AprilTag"},
    {"name": "无人机自主巡逻", "stage": "P1", "status": "pending", "desc": "Tello/RoboMaster TT"},
    {"name": "异常识别与推送", "stage": "P2", "status": "pending", "desc": "跌倒/灶火检测"},
    {"name": "空地任务派发", "stage": "P3", "status": "pending", "desc": "无人机侦察+机器狗执行"},
    {"name": "机器狗物理干预", "stage": "P4", "status": "pending", "desc": "长期目标"},
]


@app.route("/")
def index():
    return send_from_directory("web", "index.html")


# ---------------------------------------------------------------- VTuber 组件资源与 LLM 转发
VTUBER_MODEL_ROOT = os.environ.get(
    "VTUBER_MODEL_ROOT", r"E:\OpenLLMVTuber\live2d-models")


@app.route("/live2d-models/<path:asset>")
def live2d_assets(asset):
    """把本机已有的 Live2D 模型目录挂到塔台（VTuber 组件用）"""
    return send_from_directory(VTUBER_MODEL_ROOT, asset)


@app.route("/api/llm/chat", methods=["POST"])
def api_llm_chat():
    """LLM 对话转发：豆包(火山方舟)等 OpenAI 兼容 API 不允许浏览器直连(CORS)，
    前端把 key/base/model 放请求头，这里纯转发，不落盘。"""
    import urllib.request

    data = request.get_json(silent=True) or {}
    messages = data.get("messages") or []
    api_key = request.headers.get("X-LLM-Key", "").strip()
    base_url = request.headers.get("X-LLM-Base", "https://api.deepseek.com/v1").strip().rstrip("/")
    model = request.headers.get("X-LLM-Model", "deepseek-flash").strip()
    if not messages:
        return jsonify({"ok": False, "msg": "messages 为空"}), 400
    if not api_key:
        return jsonify({"ok": False, "msg": "未填写 API Key（在齿轮设置里填写）"}), 401

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": float(data.get("temperature", 0.7)),
        "max_tokens": int(data.get("max_tokens", 1024)),
    }).encode("utf-8")
    req = urllib.request.Request(
        base_url + "/chat/completions", data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            body = json.loads(r.read().decode("utf-8"))
        reply = (body.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        usage = body.get("usage") or {}
        if not reply:
            return jsonify({"ok": False, "msg": "模型返回空内容"}), 502
        return jsonify({"ok": True, "reply": reply,
                        "model": body.get("model", model),
                        "tokens": usage.get("total_tokens")})
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        return jsonify({"ok": False, "msg": f"上游 HTTP {e.code}: {detail}"}), 502
    except Exception as e:
        # DeepSeek 官方过载时：秒回 200 但正文一个字节不吐（实测流式只发 keep-alive 心跳），
        # 客户端只能靠超时识别。这里给用户一句人话，不让 UI 干等 60 秒。
        if "timed out" in str(e) or "timeout" in str(e).lower():
            return jsonify({"ok": False,
                            "msg": "DeepSeek 服务端无响应（已等 45 秒，官方疑似过载）。稍后再试，或在齿轮设置里换其它 OpenAI 兼容 API。"}), 504
        return jsonify({"ok": False, "msg": f"上游连接失败: {e}"}), 502


# ---------------------------------------------------------------- TTS（edge-tts，同款 Open-LLM-VTuber）
import asyncio
try:
    import edge_tts
except ImportError:
    edge_tts = None

# 精选中文音色（Open-LLM-VTuber 同款引擎，voice id 一致）
EDGE_TTS_VOICES = [
    {"id": "zh-CN-XiaoxiaoNeural", "label": "晓晓 · 女 · 温柔大方（推荐）"},
    {"id": "zh-CN-XiaoyiNeural", "label": "晓伊 · 女 · 活泼"},
    {"id": "zh-CN-XiaochenNeural", "label": "晓辰 · 女 · 沉稳"},
    {"id": "zh-CN-XiaohanNeural", "label": "晓涵 · 女 · 亲切"},
    {"id": "zh-CN-XiaomengNeural", "label": "晓梦 · 女 · 轻柔"},
    {"id": "zh-CN-XiaoshuangNeural", "label": "晓双 · 女童声"},
    {"id": "zh-CN-YunxiNeural", "label": "云希 · 男 · 阳光"},
    {"id": "zh-CN-YunjianNeural", "label": "云健 · 男 · 磁性浑厚"},
    {"id": "zh-CN-YunyangNeural", "label": "云扬 · 男 · 新闻播音"},
    {"id": "zh-CN-YunxiaNeural", "label": "云夏 · 男 · 少年"},
    {"id": "zh-CN-liaoning-XiaobeiNeural", "label": "晓北 · 东北口音"},
    {"id": "zh-CN-shaanxi-XiaoniNeural", "label": "晓妮 · 陕西口音"},
    {"id": "zh-HK-HiuMaanNeural", "label": "粤语 · HiuMaan（香港）"},
    {"id": "zh-TW-HsiaoChenNeural", "label": "晓臻 · 台湾口音"},
]


@app.route("/api/tts/voices")
def api_tts_voices():
    return jsonify(EDGE_TTS_VOICES)


@app.route("/api/tts", methods=["POST"])
def api_tts():
    """文本转语音：调微软 Edge 神经网络音色（与 Open-LLM-VTuber 同源），
    返回 audio/mpeg 流，前端 <Audio> 直接播放。
    rate: 50~200（100=原速）；volume: 0~100（100=原音量）；pitch: -50~+50 Hz
    """
    if edge_tts is None:
        return jsonify({"ok": False, "msg": "服务器缺少 edge-tts（pip install edge-tts）"}), 500
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()[:500]
    if not text:
        return jsonify({"ok": False, "msg": "文本为空"}), 400
    voice = data.get("voice") or "zh-CN-XiaoxiaoNeural"
    rate_delta = max(-90, min(200, int(data.get("rate", 100)) - 100))
    vol_delta = max(-100, min(100, int(data.get("volume", 100)) - 100))
    pitch_hz = max(-50, min(50, int(data.get("pitch", 0))))

    def synth():
        async def _run():
            com = edge_tts.Communicate(
                text, voice,
                rate=f"{rate_delta:+d}%",
                volume=f"{vol_delta:+d}%",
                pitch=f"{pitch_hz:+d}Hz")
            buf = bytearray()
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            return bytes(buf)
        return asyncio.run(_run())

    try:
        mp3 = synth()
        if not mp3:
            return jsonify({"ok": False, "msg": "edge-tts 返回空音频"}), 502
        return Response(mp3, mimetype="audio/mpeg",
                        headers={"Cache-Control": "no-store"})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"合成失败: {e}"}), 500


# ---------------------------------------------------------------- ASR（sherpa-onnx SenseVoice，同款 Open-LLM-VTuber 本地识别）
import io
import wave
import array as _array
try:
    import sherpa_onnx as _sherpa
    _SENSE_VOICE_DIR = r"E:\OpenLLMVTuber\models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    if os.path.isfile(os.path.join(_SENSE_VOICE_DIR, "model.int8.onnx")):
        # 新版 sherpa-onnx 用工厂方法构造（与 Open-LLM-VTuber 的 sherpa_onnx_asr.py 同款调用）
        _asr = _sherpa.OfflineRecognizer.from_sense_voice(
            model=os.path.join(_SENSE_VOICE_DIR, "model.int8.onnx"),
            tokens=os.path.join(_SENSE_VOICE_DIR, "tokens.txt"),
            num_threads=2, use_itn=True,
        )
    else:
        _asr = None
except Exception:
    _asr = None


@app.route("/api/asr", methods=["POST"])
def api_asr():
    """语音识别：接收 16bit PCM WAV（前端已转 16kHz 单声道），
    用本地 SenseVoice 模型转文字（中英日韩粤，离线免费）。
    """
    if _asr is None:
        return jsonify({"ok": False, "msg": "ASR 模型未加载（缺 sherpa-onnx 或 SenseVoice 模型）"}), 500
    wav_bytes = request.get_data()
    if not wav_bytes:
        return jsonify({"ok": False, "msg": "音频为空"}), 400
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if sw != 2:
            return jsonify({"ok": False, "msg": f"仅支持 16bit PCM WAV（收到 {sw * 8}bit）"}), 400
        pcm = _array.array("h")
        pcm.frombytes(raw)
        if ch > 1:  # 多声道取左声道
            pcm = _array.array("h", pcm[0::ch])
        samples = _array.array("f", [s / 32768.0 for s in pcm])
        stream = _asr.create_stream()
        stream.accept_waveform(sr, samples)
        _asr.decode_stream(stream)
        text = stream.result.text.strip()
        return jsonify({"ok": True, "text": text})
    except Exception as e:
        return jsonify({"ok": False, "msg": f"识别失败: {e}"}), 500


@app.route("/api/vision/restart", methods=["POST"])
def api_vision_restart():
    """转发到本机 vision_watchdog_server（:8091 POST /restart）真正拉起视觉链路。"""
    import urllib.error
    import urllib.request

    url = "http://127.0.0.1:8091/restart"
    try:
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = {"raw": raw}
            started = body.get("started") if isinstance(body, dict) else None
            alive = body.get("all") if isinstance(body, dict) else None
            msg = "视觉守护已执行重启检查"
            if started:
                msg += "；新拉起: " + ", ".join(str(x) for x in started)
            elif isinstance(alive, dict):
                msg += "；当前进程均在线"
            return jsonify({
                "ok": True,
                "msg": msg,
                "watchdog": body,
            })
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        return jsonify({
            "ok": False,
            "msg": f"视觉守护返回 HTTP {e.code}",
            "error": detail or str(e),
        }), 502
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        return jsonify({
            "ok": False,
            "msg": "无法连接视觉守护(127.0.0.1:8091)，请确认 vision_watchdog_server 已启动",
            "error": str(reason),
        }), 503
    except TimeoutError:
        return jsonify({
            "ok": False,
            "msg": "视觉守护重启超时（5s）",
            "error": "timeout",
        }), 504
    except Exception as e:
        return jsonify({
            "ok": False,
            "msg": f"视觉重启失败: {e}",
            "error": str(e),
        }), 500


def _kill_proc_cmdline(image_name, keyword):
    """按进程名 + 命令行关键词杀进程，返回结束数量。优先 psutil，无则退回 taskkill。"""
    killed = 0
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                if p.info["name"] and image_name.lower() not in p.info["name"].lower():
                    continue
                cl = " ".join(p.info["cmdline"] or [])
                if keyword.lower() in cl.lower():
                    p.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    elif image_name.lower() == "kinect_bridge.exe":
        r = subprocess.run(["taskkill", "/IM", "kinect_bridge.exe", "/F"],
                           capture_output=True)
        killed = 1 if r.returncode == 0 else 0
    return killed


@app.route("/api/kinect/power", methods=["GET", "POST"])
def api_kinect_power():
    """Kinect 总开关。

    关闭：写 kinect_off.flag（keeper 见到标志即不再拉起/自愈）+ 立即结束
    bridge / yolo3d 进程，设备随之断电空闲，不会再"开了关不上"。
    开启：删标志，keeper 下一轮巡检（≤15 秒）自动拉起链路。
    """
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        on = bool(data.get("on", True))
        os.makedirs(KINECT_RUNTIME_DIR, exist_ok=True)
        if on:
            try:
                os.remove(KINECT_OFF_FLAG)
            except FileNotFoundError:
                pass
            return jsonify({"ok": True, "on": True,
                            "msg": "已开启 Kinect：视觉守护将在 15 秒内拉起链路"})
        with open(KINECT_OFF_FLAG, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        n = _kill_proc_cmdline("kinect_bridge.exe", "kinect_bridge")
        n += _kill_proc_cmdline("python.exe", "kinect_yolo3d")
        return jsonify({"ok": True, "on": False,
                        "msg": f"已关闭 Kinect：结束 {n} 个进程，守护不会再拉起"})
    return jsonify({"ok": True, "on": not os.path.exists(KINECT_OFF_FLAG)})


@app.route("/api/status")
def api_status():
    """主状态接口：前端每 2 秒轮询"""
    with _probe_lock:
        probe = dict(PROBE)

    uav_online = probe["online"]
    uav = {
        "count": 1 if uav_online else 0,
        "list": [{"name": "Tello", "ssid": TELLO_SSID, "ip": TELLO_IP}] if uav_online else [],
        "battery": probe["battery"],
        "wlan": probe["wlan"],
        "apipa": probe.get("apipa"),
        "hint": probe.get("hint"),
        "wlan_signal": wifi_signal(),
        "online": uav_online,
        "temp": tello_temp(),
        "temp_warn": TELLO_TEMP_WARN,
        "temp_hard": TELLO_TEMP_HARD,
    }
    return jsonify({
        "tower": {
            "online": True,
            "hostname": socket.gethostname(),
            "ip": get_local_ip(),
            "cpu_temp": read_cpu_temp(),
            "memory": get_mem_info(),
            "disk": get_disk_info(),
            "uptime": get_uptime(),
            "arch": platform.machine(),
        },
        "kinect": kinect_status(),
        "kinect_frame": kinect_frame_status(),
        "kinect_power": not os.path.exists(KINECT_OFF_FLAG),
        "uav": uav,
        "uav_video": video_status(),
        "vision": vision_status(),
        "ugv": STATE["ugv"],
        "missions": STATE["missions"][-20:],  # 最近 20 条
        "capabilities": CAPABILITIES,
    })


@app.route("/api/mission", methods=["POST"])
def api_mission():
    """接收测试任务（演示用）"""
    global _MISSION_SEQ
    from flask import request
    data = request.get_json(silent=True) or {}
    _MISSION_SEQ += 1
    task = {
        "id": _MISSION_SEQ,
        "time": time.strftime("%H:%M:%S"),
        "type": data.get("type", "unknown"),
        "target": data.get("target", "-"),
        "status": "received",
    }
    STATE["missions"].append(task)
    return jsonify({"ok": True, "task": task})


@app.route("/api/mission/<int:mission_id>", methods=["DELETE"])
def api_mission_delete(mission_id):
    """删除当前内存中的一条任务记录。"""
    for index, task in enumerate(STATE["missions"]):
        if task.get("id") == mission_id:
            deleted = STATE["missions"].pop(index)
            return jsonify({"ok": True, "task": deleted})
    return jsonify({"ok": False, "msg": "任务不存在或已删除"}), 404


# ---------------------------------------------------------------- AI 管家对话（小灵）
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-flash"

BUTLER_SYSTEM = """你是"小灵"，这个家庭安全调度塔台的 AI 管家。用简洁友好的中文回答，一般 1-4 句。
你身后有全家实时传感数据（附在系统状态里）。当用户问"家里有什么/看到什么/现在情况"时，
必须基于状态数据回答，绝不编造。描述物体位置用"前方 X 米、偏左/右 Y 米"这类人话，不要输出裸坐标。
设备不在线或视频未开启就如实说明。偶尔可以主动提醒安全注意事项，但不要啰嗦。"""


def _butler_status_digest():
    """把塔台实时状态压缩成给 LLM 看的简报"""
    try:
        resp = api_status()
        st = json.loads(resp.get_data(as_text=True))
    except Exception as e:
        return f"(状态获取失败: {e})"

    vision = st.get("vision") or {}
    objs = vision.get("objects") or []
    change = vision.get("change") or {}
    uav = st.get("uav") or {}
    tower = st.get("tower") or {}
    kinect = st.get("kinect") or {}
    kinect_frame = st.get("kinect_frame") or {}

    lines = []
    lines.append(f"塔台: 在线, CPU温度 {tower.get('cpu_temp')}°C, 内存 {((tower.get('memory') or {}).get('percent') or '?')}%")
    lines.append(f"Kinect视觉节点: {'在线' if kinect.get('available') else '离线'}"
                 f"({kinect.get('note') or '无备注'})")
    cam_age = kinect_frame.get("age_s")
    lines.append(f"摄像头画面: {'实时' if isinstance(cam_age, (int, float)) and cam_age < 3 else '未就绪'}")
    if objs:
        obj_txt = "、".join(
            f"{o.get('label')}(置信度{o.get('conf')}, " +
            (f"前方{o.get('z')}米" if o.get("z") is not None else "无深度") + ")"
            for o in objs[:10]
        )
        lines.append(f"当前视野内({len(objs)}个目标): {obj_txt}")
        lines.append(f"画面变化度: {change.get('level') or '未知'}")
    else:
        lines.append("当前视野内: 没有识别到目标")
    lines.append(f"Tello无人机: {'在线, 电量' + str(uav.get('battery')) + '%' if uav.get('online') else '未连接'}")
    lines.append(f"Tello视频: {'开启' if (st.get('uav_video') or {}).get('running') else '未开启'}")
    return "\n".join(lines)


def _butler_offline_reply(message):
    """DeepSeek 不可用时的规则兜底：基于实时数据回答，绝不编造"""
    try:
        st = json.loads(api_status().get_data(as_text=True))
    except Exception:
        return "（离线模式）我连塔台状态都读不到了，请检查塔台服务。"
    vision = st.get("vision") or {}
    objs = vision.get("objects") or []
    uav = st.get("uav") or {}
    kinect = st.get("kinect") or {}
    kinect_frame = st.get("kinect_frame") or {}
    m = message

    def human(o):
        if o.get("z") is None:
            return f"{o.get('label')}（未测到距离）"
        x, z = o.get("x") or 0, o.get("z")
        side = "右边" if x > 0.15 else ("左边" if x < -0.15 else "正中")
        return f"{o.get('label')}，前方约 {z} 米、偏{side}"

    if any(k in m for k in ("有什么", "看到", "看见", "识别", "视野", "谁在", "家里有")):
        if not kinect.get("available"):
            return "Kinect 视觉节点现在离线，我暂时看不到家里。守护程序会自动尝试恢复它，恢复后你再问我一次。"
        if not objs:
            return "我正看着家里，但视野内没识别到目标——可能没人经过。"
        parts = "；".join(human(o) for o in objs[:8])
        people = sum(1 for o in objs if o.get("label") == "person")
        head = f"视野里有 {len(objs)} 个目标" + (f"（其中 {people} 个人）" if people else "") + "："
        return head + parts + "。"
    if any(k in m for k in ("你好", "在吗", "嗨", "hello", "hi")):
        return f"你好呀，我在。Kinect {'在线' if kinect.get('available') else '离线'}，Tello {'在线' if uav.get('online') else '未连接'}。想了解家里情况就问我～"
    if any(k in m for k in ("无人机", "tello", "Tello", "飞行")):
        if uav.get("online"):
            return f"Tello 在线，电量 {uav.get('battery')}%。"
        return "Tello 目前未连接。要使用无人机请先开启它的电源并连上它的热点。"
    if any(k in m for k in ("状态", "情况", "怎么样", "正常", "总结")):
        cam_ok = isinstance(kinect_frame.get("age_s"), (int, float)) and kinect_frame["age_s"] < 3
        return (f"当前概况：塔台在线；Kinect {'在线，画面' + ('实时' if cam_ok else '未就绪') if kinect.get('available') else '离线'}；"
                f"视野内 {len(objs)} 个目标；Tello {'在线' if uav.get('online') else '未连接'}。")
    return "（离线模式）云端大脑暂时连不上，我现在只能回答家里状态类问题，比如\"家里有什么\"\"现在情况怎么样\"。"


@app.route("/api/butler/chat", methods=["POST"])
def api_butler_chat():
    """AI 管家对话：注入塔台实时状态，DeepSeek 生成回答；云端不可用时离线兜底"""
    import urllib.request

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []
    if not message:
        return jsonify({"ok": False, "reply": "想问什么呀？"})

    if not DEEPSEEK_API_KEY:
        return jsonify({
            "ok": False,
            "reply": "未配置 API Key",
            "msg": "未配置 API Key：请设置环境变量 DEEPSEEK_API_KEY 后重启塔台",
            "error": "missing_api_key",
        }), 503

    digest = _butler_status_digest()
    messages = [{"role": "system",
                 "content": BUTLER_SYSTEM + "\n\n【当前家庭实时状态】\n" + digest}]
    for h in history[-8:]:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    payload = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 500,
    }).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            body = json.loads(r.read().decode("utf-8"))
        reply = (body.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not reply:
            reply = "（大脑开小差了，再问一次？）"
        return jsonify({"ok": True, "reply": reply})
    except Exception:
        # 云端大脑不可达 → 离线兜底（基于实时数据，绝不编造）
        return jsonify({"ok": True, "reply": _butler_offline_reply(message), "offline": True})


if __name__ == "__main__":
    # 后台线程在所有定义就绪后启动
    threading.Thread(target=_probe_loop, daemon=True).start()
    threading.Thread(target=_state_loop, daemon=True).start()
    threading.Thread(target=_pose_loop, daemon=True).start()
    threading.Thread(target=_rc_udp_loop, daemon=True).start()
    threading.Thread(target=_keepalive_loop, daemon=True).start()
    threading.Thread(target=_video_watchdog_loop, daemon=True).start()
    threading.Thread(target=_annotated_frame_loop, daemon=True).start()
    ip = get_local_ip()
    print(f"\n  塔台界面已启动")
    print(f"  本机访问:  http://localhost:8080")
    print(f"  局域网访问: http://{ip}:8080   ← 手机/其他设备用这个\n")
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)










