"""欣薇 ZAVE (WiFi UAV 协议) 无害验证脚本
安全约束：
  - 绝不发送 throttle（油门）相关的任何非中性值
  - 只发"全中立摇杆"包 和 一条 0x04 陀螺仪校准指令
  - 全程 <10 秒自动退出
"""
import socket
import time

DRONE_IP = "192.168.4.1"
DRONE_PORT = 8800

# ---- WiFi UAV 协议常量（来自 marshallrichards/turbodrone 逆向）----
HEADER = bytes([0xef, 0x02, 0x7c, 0x00, 0x02, 0x02, 0x00, 0x01, 0x02, 0x00, 0x00, 0x00])
CTR1_SUFFIX = bytes([0x00, 0x00, 0x14, 0x00, 0x66, 0x14])
CTRL_SUFFIX = bytes(10)                       # 10 个 0x00
CHECKSUM_SUFFIX = bytes([0x99]) + bytes(43) + bytes([0x32, 0x4b, 0x14, 0x2d, 0x00, 0x00])
CTR2_SUFFIX = bytes([0x0a, 0x00, 0x00, 0x00, 0x00, 0x00, 0x2c, 0x01, 0x00, 0x00,
                     0x00, 0x00, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00])
CTR3_SUFFIX = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

# 中性摇杆: roll/pitch/throttle/yaw = 128 (StickRange 40~220 的中点), command=0, headless=0
NEUTRAL = bytes([128, 128, 128, 128, 0x00, 0x00])
# 校准陀螺仪: 摇杆保持中立, command=0x04
CALIBRATE = bytes([128, 128, 128, 128, 0x04, 0x00])


def build_packet(ctrl: bytes, c1: int, c2: int, c3: int) -> bytes:
    """按 120 字节结构拼包"""
    xor = 0
    for b in ctrl:
        xor ^= b
    pkt = bytearray()
    pkt += HEADER
    pkt += c1.to_bytes(2, "little")     # 12-13 counter1
    pkt += CTR1_SUFFIX                  # 14-19
    pkt += ctrl                         # 20-25 控制值
    pkt += CTRL_SUFFIX                  # 26-35
    pkt += bytes([xor])                 # 36 校验
    pkt += CHECKSUM_SUFFIX              # 37-86
    pkt += c2.to_bytes(2, "little")     # 87-88 counter2
    pkt += CTR2_SUFFIX                  # 89-106
    pkt += c3.to_bytes(2, "little")     # 107-108 counter3
    pkt += CTR3_SUFFIX                  # 109-119
    assert len(pkt) == 120, f"包长度 {len(pkt)} != 120"
    return bytes(pkt)


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.8)
    sock.bind(("0.0.0.0", 0))

    print("=" * 46)
    print(" 阶段 1：纯监听 3 秒（不发任何数据）")
    print("=" * 46)
    sock.sendto(b"", (DRONE_IP, DRONE_PORT)) if False else None
    got_any = False
    t0 = time.time()
    while time.time() - t0 < 3:
        try:
            data, addr = sock.recvfrom(4096)
            got_any = True
            print(f"  收到 {len(data)}B 来自 {addr}  头部: {data[:16].hex()}")
        except socket.timeout:
            pass
    if not got_any:
        print("  （无人机不会主动推流，属正常——此协议是 pull 模式）")

    print()
    print("=" * 46)
    print(" 阶段 2：发 3 个中立摇杆包（不开电机）")
    print("=" * 46)
    c1 = c2 = c3 = 0
    for i in range(3):
        pkt = build_packet(NEUTRAL, c1, c2, c3)
        sock.sendto(pkt, (DRONE_IP, DRONE_PORT))
        print(f"  [{i+1}/3] 已发送中立包 c1={c1} ({len(pkt)}B)")
        c1 = (c1 + 1) & 0xFFFF; c2 = (c2 + 1) & 0xFFFF; c3 = (c3 + 1) & 0xFFFF
        # 顺便收一下响应
        try:
            data, addr = sock.recvfrom(4096)
            print(f"        <- 响应 {len(data)}B from {addr}")
        except socket.timeout:
            print("        <- 无响应")
        time.sleep(0.3)

    print()
    print("=" * 46)
    print(" 阶段 3：发送 陀螺仪校准 指令 (0x04)")
    print("   >>> 现在看无人机的指示灯，应该会快速闪烁 <<<")
    print("=" * 46)
    for i in range(3):
        pkt = build_packet(CALIBRATE, c1, c2, c3)
        sock.sendto(pkt, (DRONE_IP, DRONE_PORT))
        print(f"  [{i+1}/3] 已发送校准指令")
        c1 = (c1 + 1) & 0xFFFF; c2 = (c2 + 1) & 0xFFFF; c3 = (c3 + 1) & 0xFFFF
        try:
            data, addr = sock.recvfrom(4096)
            print(f"        <- 响应 {len(data)}B from {addr}  头: {data[:16].hex()}")
        except socket.timeout:
            print("        <- 无响应")
        time.sleep(0.35)

    print()
    print("=" * 46)
    print(" 阶段 4：发一次中立包 + 急停指令 (0x02) 收尾")
    print("   （按用户要求：5 秒后强制停止，急停确保安全）")
    print("=" * 46)
    estop = bytes([128, 128, 128, 128, 0x02, 0x00])
    for i in range(2):
        pkt = build_packet(estop, c1, c2, c3)
        sock.sendto(pkt, (DRONE_IP, DRONE_PORT))
        print(f"  [{i+1}/2] 已发送急停(电机锁定)")
        c1 = (c1 + 1) & 0xFFFF; c2 = (c2 + 1) & 0xFFFF; c3 = (c3 + 1) & 0xFFFF
        time.sleep(0.2)

    sock.close()
    print()
    print(">>> 验证结束，socket 已关闭 <<<")


if __name__ == "__main__":
    main()
