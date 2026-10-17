# tello_video_test.py - verify camera stream (no flight)
import socket, time

TELLO = ("192.168.10.1", 8889)
cmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
cmd.bind(("0.0.0.0", 8889))
cmd.settimeout(5)

def send(c):
    cmd.sendto(c.encode(), TELLO)
    try:
        r, _ = cmd.recvfrom(2048)
        print(f"> {c:10s} => {r.decode(errors='replace').strip()}")
        return r
    except socket.timeout:
        print(f"> {c:10s} => TIMEOUT")
        return b""

vid = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
vid.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
vid.bind(("0.0.0.0", 11111))
vid.settimeout(1)

send("command")
time.sleep(0.3)
r = send("streamon")
if b"ok" not in r.lower() and b"OK" not in r:
    print("streamon rejected, abort")
    raise SystemExit(1)

print("collecting video packets for 5s...")
t0 = time.time(); nbytes = 0; npkts = 0
while time.time() - t0 < 5:
    try:
        data, _ = vid.recvfrom(4096)
        nbytes += len(data); npkts += 1
    except socket.timeout:
        pass

send("streamoff")
cmd.close(); vid.close()

kbps = nbytes * 8 / 1000 / 5
print(f"\nvideo: {npkts} packets, {nbytes/1024:.0f} KB in 5s = {kbps:.0f} kbps")
print("RESULT:", "CAMERA OK - H.264 stream flowing" if nbytes > 100000 else "CAMERA SUSPECT - little/no data")
