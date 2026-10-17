"""第二轮探测：多假设并行验证
不起飞、不碰油门。发的东西全是无害探测：
  A. 8800 端口中立包（上轮已试，换校验范围重试）
  B. 8888 端口（S2x 家族视频口，顺便探测）
  C. 视频 pull 请求包（REQUEST_A，看有没有 JPEG 数据回来）
  D. 9060 端口（部分 WiFi UAV 变体）
每个假设发 2 次，监听 0.6s，全部打印十六进制头部。
"""
import socket, time

DRONE = "192.168.4.1"

HEADER = bytes([0xef, 0x02, 0x7c, 0x00, 0x02, 0x02, 0x00, 0x01, 0x02, 0x00, 0x00, 0x00])
CTR1_S = bytes([0x00, 0x00, 0x14, 0x00, 0x66, 0x14])
CTRL_S = bytes(10)
CHECK_S = bytes([0x99]) + bytes(43) + bytes([0x32, 0x4b, 0x14, 0x2d, 0x00, 0x00])
CTR2_S = bytes([0x0a, 0x00, 0x00, 0x00, 0x00, 0x00, 0x2c, 0x01, 0x00, 0x00,
                0x00, 0x00, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00])
CTR3_S = bytes(11)
NEUTRAL = bytes([128, 128, 128, 128, 0, 0])

# turbodrone 的 REQUEST_A 视频拉流包（88B，帧号在 [12:13]）
REQ_A = bytearray(88)
REQ_A[0] = 0xef; REQ_A[1] = 0x02; REQ_A[2] = 0x7c; REQ_A[3] = 0x00
REQ_A[4] = 0x02; REQ_A[5] = 0x02; REQ_A[6] = 0x00; REQ_A[7] = 0x01
REQ_A[8] = 0x02; REQ_A[9] = 0x00

def pkt120(ctrl=NEUTRAL, c1=0, c2=0, c3=0, xor_all=True):
    x = 0
    for b in ctrl: x ^= b
    p = bytearray()
    p += HEADER
    p += c1.to_bytes(2,"little") + CTR1_S
    p += ctrl + CTRL_S
    p += bytes([x])
    p += CHECK_S
    p += c2.to_bytes(2,"little") + CTR2_S
    p += c3.to_bytes(2,"little") + CTR3_S
    return bytes(p)

def try_send(sock, label, payload, addr, n=2, wait=0.6):
    print(f"\n--- {label} -> {addr} ---")
    for i in range(n):
        try:
            sock.sendto(payload, addr)
            print(f"  sent #{i+1} ({len(payload)}B)")
        except Exception as e:
            print(f"  send err: {e}"); return
        t0 = time.time()
        while time.time() - t0 < wait:
            sock.settimeout(max(0.05, wait - (time.time()-t0)))
            try:
                data, ra = sock.recvfrom(65535)
                print(f"  << {len(data)}B from {ra}  head: {data[:24].hex()}")
                if len(data) > 100:
                    print(f"     ...(数据量大，疑似视频流)")
                return
            except socket.timeout:
                continue
    print("  (无响应)")

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 0))
    print("本地端口:", s.getsockname()[1])

    # A: 8800 中立包（重试，这次打 3 个计数器变体）
    try_send(s, "A1 8800 中立包 ctr=0", pkt120(c1=0,c2=0,c3=0), (DRONE,8800))
    try_send(s, "A2 8800 中立包 ctr=100", pkt120(c1=100,c2=100,c3=100), (DRONE,8800))
    try_send(s, "A3 8800 中立包 ctr=0x1414", pkt120(c1=0x1414,c2=0x1414,c3=0x1414), (DRONE,8800))

    # B: 8888 (S2x 视频)
    try_send(s, "B 8888 空探测", b"\xcc\xaa\x55\x00", (DRONE,8888))

    # C: 8800 视频拉流 REQUEST_A frame0
    ra = bytes(REQ_A[:12]) + (0).to_bytes(2,"little") + bytes(REQ_A[14:])
    try_send(s, "C 8800 REQUEST_A(f=0)", ra, (DRONE,8800))
    try_send(s, "C2 8888 REQUEST_A(f=0)", ra, (DRONE,8888))

    # D: 9060 变体端口
    try_send(s, "D 9060 中立包", pkt120(), (DRONE,9060))

    # 最后: 广播嗅探 2 秒——无人机有没有向任何端口主动发包
    print("\n--- E: 混合监听 2.5s（无人机主动发的包）---")
    s.settimeout(2.5)
    t0=time.time(); hits=0
    while time.time()-t0 < 2.5:
        try:
            data, ra = s.recvfrom(65535)
            hits += 1
            print(f"  << {len(data)}B from {ra} head: {data[:24].hex()}")
        except socket.timeout:
            break
    if not hits: print("  (无主动包)")
    s.close()
    print("\n>>> 探测结束 <<<")

if __name__ == "__main__":
    main()
