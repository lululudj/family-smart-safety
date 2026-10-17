# tello_probe.py - Tello SDK handshake + read-only status probe
# SAFETY: query commands only. No takeoff/motor commands anywhere in this file.
import socket, threading, time, sys

TELLO = ("192.168.10.1", 8889)
CMDS = ["command", "sdk?", "sn?", "battery?", "time?", "wifi?"]

state_lines = []
def state_listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", 8890))
    except OSError as e:
        print(f"[state] bind 8890 failed: {e}")
        return
    s.settimeout(6)
    t0 = time.time()
    while time.time() - t0 < 6:
        try:
            data, _ = s.recvfrom(2048)
            state_lines.append(data.decode(errors="replace").strip())
        except socket.timeout:
            break
    s.close()

th = threading.Thread(target=state_listener, daemon=True)
th.start()
time.sleep(0.3)

cmd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    cmd.bind(("0.0.0.0", 8889))
except OSError as e:
    print(f"[cmd] bind 8889 failed: {e} (another tello app running? close DJI Tello app)")
    sys.exit(1)
cmd.settimeout(5)

ok = True
for c in CMDS:
    try:
        cmd.sendto(c.encode(), TELLO)
        resp, addr = cmd.recvfrom(2048)
        print(f"> {c:10s} => {resp.decode(errors='replace').strip()}")
    except socket.timeout:
        print(f"> {c:10s} => TIMEOUT (no response)")
        ok = False
        break
    time.sleep(0.3)

cmd.close()
th.join(timeout=7)

if state_lines:
    print(f"\n[state broadcast] {len(state_lines)} packets in 6s, latest:")
    print("  " + state_lines[-1])
    kv = dict(p.split(":") for p in state_lines[-1].split(";") if ":" in p)
    for k in ["bat", "temph", "templ", "tof", "h", "baro", "time"]:
        if k in kv:
            print(f"  {k:6s} = {kv[k]}")
else:
    print("\n[state broadcast] none received")

print("\nRESULT:", "TELLO LINK OK" if ok else "LINK FAIL")
