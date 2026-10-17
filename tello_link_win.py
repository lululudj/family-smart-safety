#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 端 Tello 链路助手（塔台跑在本机时用）。

解决的问题：
    Tello AP 的 DHCP 在 Windows 上经常不给真地址 —— 连上了热点，
    无线网卡却只拿到 169.254.x.x（APIPA 假地址）。此时本机和 Tello
    不在同一网段，UDP 包全部走默认网关跑丢，表现就是"连上了却连不上"。

用法（系统 Python 3.12）：
    py tello_link_win.py              # 一键：扫描 → 连接 → 修 IP → 握手验证
    py tello_link_win.py --scan       # 只扫描附近的 TELLO-* 热点
    py tello_link_win.py --status     # 只看当前链路状态（不改任何东西）
    py tello_link_win.py --fix-ip     # 强制给无线网卡设静态 192.168.10.2（需管理员）
    py tello_link_win.py --iface WLAN # 手动指定无线网卡名

注意：本脚本只动无线网卡，绝不碰以太网（家里上网那张）。
"""

import argparse
import os
import re
import socket
import subprocess
import sys
import time

# Windows 控制台默认 GBK。这里保留原编码（改编码会让 pipe 那头解出乱码），
# 只把错误处理放宽 —— 所以本文件的标记一律用 ASCII（[OK]/[X]/[!]），别用 emoji。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

TELLO_PREFIX = "TELLO-"
TELLO_IP = "192.168.10.1"
TELLO_CMD_PORT = 8889
STATIC_IP = "192.168.10.2"
NETMASK = "255.255.255.0"


def sh(args, timeout=20):
    """跑一条命令，自动处理中文代码页输出"""
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
        raw = (p.stdout or b"") + (p.stderr or b"")
    except subprocess.TimeoutExpired:
        return ""
    except FileNotFoundError:
        return ""
    for enc in ("gbk", "utf-8", "mbcs"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "ignore")


def powershell(cmd):
    return sh(["powershell", "-NoProfile", "-Command", cmd])


def wlan_iface(explicit=None):
    """自动找出物理无线网卡的接口名"""
    if explicit:
        return explicit
    out = powershell(
        "Get-NetAdapter -Physical | Where-Object {$_.MediaType -eq 'Native 802.11'} "
        "| Select-Object -ExpandProperty Name"
    )
    names = [l.strip() for l in out.splitlines() if l.strip()]
    return names[0] if names else None


def iface_ips(iface):
    """某网卡上的所有 IPv4 地址"""
    out = powershell(
        f"Get-NetIPAddress -InterfaceAlias '{iface}' -AddressFamily IPv4 "
        f"| Select-Object -ExpandProperty IPAddress"
    )
    return [l.strip() for l in out.splitlines() if re.match(r"^\d+\.\d+\.\d+\.\d+$", l.strip())]


def admin_hint():
    """提示是否需要管理员权限（设静态 IP 需要）"""
    try:
        import ctypes
        return not ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def scan_ssids():
    """扫描可见 SSID"""
    out = sh(["netsh", "wlan", "show", "networks"])
    ssids = []
    for line in out.splitlines():
        m = re.search(r"SSID\s*\d+\s*:\s*(.+?)\s*$", line)
        if m:
            ssids.append(m.group(1).strip())
    return ssids


def find_tello(ssids):
    hits = [s for s in ssids if s.upper().startswith(TELLO_PREFIX)]
    return hits


def connect(ssid):
    """连到一个开放热点"""
    return sh(["netsh", "wlan", "connect", f"name={ssid}"], timeout=25)


def wait_for_ip(iface, timeout=20):
    """等 DHCP 给地址；拿到 192.168.10.x 即成功"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ips = iface_ips(iface)
        good = [i for i in ips if i.startswith("192.168.10.")]
        if good:
            return good[0]
        time.sleep(1)
    return None


def set_static_ip(iface):
    """给无线网卡设静态 IP（需要管理员）。返回是否成功"""
    out = sh(["netsh", "interface", "ipv4", "set", "address",
              f"name={iface}", "static", STATIC_IP, NETMASK, TELLO_IP], timeout=25)
    ok = "确定" in out or "ok" in out.lower() or out.strip() == ""
    # 等路由生效
    time.sleep(3)
    return ok, out


def tello_cmd(cmd, timeout=5.0, retries=1):
    """发 SDK 命令并等文本回复（对齐塔台里那份实现）"""
    last = None
    for _ in range(retries):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.bind(("", 0))
            s.sendto(cmd.encode(), (TELLO_IP, TELLO_CMD_PORT))
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    data, _ = s.recvfrom(2048)
                except socket.timeout:
                    break
                text = data.decode("ascii", "ignore").strip()
                if text:
                    return text
            last = f"等待 {cmd!r} 回复超时"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        finally:
            s.close()
    raise RuntimeError(last)


def handshake():
    """command 握手 + 读电量"""
    r1 = tello_cmd("command", timeout=6.0, retries=2)
    ok_hands = "ok" in r1.lower()
    batt = None
    if ok_hands:
        try:
            raw = tello_cmd("battery?", timeout=6.0, retries=2)
            m = re.search(r"(\d{1,3})", raw)
            if m:
                batt = int(m.group(1))
        except Exception:
            pass
    return ok_hands, batt


def ping_ok():
    p = subprocess.run(["ping", "-n", "2", "-w", "1500", TELLO_IP],
                       capture_output=True)
    return p.returncode == 0


def show_status(iface):
    print("=" * 62)
    print(f"无线网卡：{iface}")
    ips = iface_ips(iface)
    print(f"  该网卡 IPv4：{', '.join(ips) if ips else '（无）'}")
    good = [i for i in ips if i.startswith("192.168.10.")]
    apipa = [i for i in ips if i.startswith("169.254.")]
    if good:
        print(f"  [OK] 已在 Tello 网段：{good[0]}")
    elif apipa:
        print(f"  [X] 只有假地址 {apipa[0]} —— 连上了热点但 DHCP 没给地址，"
              f"需要静态 IP（跑 --fix-ip，需管理员）")
    else:
        print("  [X] 不在 Tello 网段 —— 请先开机并连上 TELLO-* 热点")
    # 当前连的 SSID
    out = sh(["netsh", "wlan", "show", "interfaces"])
    m = re.search(r"SSID\s*:\s*(.+?)\s*$", out, re.M)
    # 上面会先匹配到 "BSSID" 之类，取最后出现的 SSID 行更准
    ssids = re.findall(r"^\s*SSID\s*:\s*(.+?)\s*$", out, re.M)
    print(f"  当前 SSID：{ssids[-1] if ssids else '（未连接）'}")
    print(f"  ping {TELLO_IP}：{'通' if ping_ok() else '不通'}")
    print("=" * 62)
    return bool(good)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="只扫描 TELLO-* 热点")
    ap.add_argument("--status", action="store_true", help="只看链路状态")
    ap.add_argument("--fix-ip", action="store_true", help="强制设静态 IP（需管理员）")
    ap.add_argument("--iface", help="手动指定无线网卡名")
    ap.add_argument("--ssid", help="手动指定 Tello 热点名（跳过扫描）")
    args = ap.parse_args()

    iface = wlan_iface(args.iface)
    if not iface:
        print("[X] 没找到物理无线网卡。请用 --iface 手动指定。")
        return 1

    if args.status:
        show_status(iface)
        return 0

    if args.fix_ip:
        if admin_hint():
            print("[X] 需要管理员权限：请右键 PowerShell / 终端 → 以管理员身份运行，再执行。")
            return 1
        ok, out = set_static_ip(iface)
        print("静态 IP 设置：", "成功" if ok else f"可能失败 -> {out.strip()[:200]}")
        show_status(iface)
        return 0

    if args.scan or True:
        print(f"扫描热点中（网卡 {iface}）...")
        ssids = scan_ssids()
        tellos = find_tello(ssids)
        if not tellos:
            print(f"[X] 没找到 {TELLO_PREFIX}* 热点。")
            print("   请确认：1) Tello 电池有电  2) 已开机等黄灯闪后变绿闪  3) 距离够近")
            print(f"   当前可见 {len(ssids)} 个网络：{', '.join(ssids[:8])}")
            if args.scan:
                return 1
            return 1
        ssid = args.ssid or tellos[0]
        print(f"[OK] 找到 Tello 热点：{ssid}")
        print("连接中...")
        out = connect(ssid)
        if "成功" not in out and "successfully" not in out.lower() and "已完成" not in out:
            print(f"   连接命令返回：{out.strip()[:200]}")

    print("等待 DHCP 分配地址...")
    ip = wait_for_ip(iface, timeout=20)
    if not ip:
        print("[X] 20 秒内没拿到 192.168.10.x，尝试静态 IP 兜底")
        if admin_hint():
            print("[X] 需要管理员权限才能设静态 IP。")
            print("   请以管理员身份终端重跑： py tello_link_win.py --fix-ip")
            show_status(iface)
            return 1
        ok, out = set_static_ip(iface)
        print("   静态 IP：", "成功" if ok else f"失败 {out.strip()[:160]}")
        ip = wait_for_ip(iface, timeout=8)

    if not ip:
        show_status(iface)
        print("[X] 仍不在 Tello 网段。可能需要重新插拔或检查 Tello 是否真的在广播热点。")
        return 1

    print(f"[OK] 本机 Tello 链路地址：{ip}")
    print("SDK 握手中...")
    try:
        okh, batt = handshake()
    except Exception as e:
        okh, batt = False, None
        print(f"   握手异常：{e}")

    if okh:
        print(f"[OK] Tello 应答正常" + (f"，电量 {batt}%" if batt is not None else ""))
        print("   现在刷新塔台页面 http://127.0.0.1:8080/ ，无人机状态应该转为在线。")
        return 0
    else:
        print("[!] 网络层通了但 SDK 无应答 —— 典型是 Tello 命令通道冻结。")
        print("   处理：让 Tello 完全断电（长按电源键）→ 重新开机 → 再跑一次本脚本。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
