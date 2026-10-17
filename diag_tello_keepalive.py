# -*- coding: utf-8 -*-
"""决定性诊断：固定源端口发 command，排空并逐包打印所有回包。"""
import socket
import time

TELLO = ("192.168.10.1", 8889)
LOCAL_IP = "192.168.10.2"


def mk(src_ip, src_port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.ioctl(socket.SIO_UDP_CONNRESET, False)
    except Exception:
        pass
    s.bind((src_ip, src_port))
    s.settimeout(1.0)
    return s


def drain(s, seconds):
    """打印窗口内收到的每一个包。"""
    t0 = time.time()
    got = []
    while time.time() - t0 < seconds:
        try:
            d, addr = s.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError as e:
            print(f"    OSError {e}")
            continue
        got.append((addr, d))
        print(f"    <- from {addr} len={len(d)} repr={d[:60]!r}")
    return got


def trial(name, src_ip, src_port, rounds=3):
    print(f"[{name}] bind {src_ip or '0.0.0.0'}:{src_port}")
    s = mk(src_ip, src_port)
    for i in range(rounds):
        print(f"  -- 第{i}次 sendto('command') --")
        s.sendto(b"command", TELLO)
        drain(s, 3.0)
        time.sleep(0.5)
    s.close()


trial("A 固定8889", LOCAL_IP, 8889, rounds=3)
print()
trial("B 随机端口", LOCAL_IP, 0, rounds=3)
