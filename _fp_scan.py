from pathlib import Path
import re
p = Path(r"E:\全家智能安全调度系统\kinect_yolo3d.py")
t = p.read_text(encoding="utf-8")
log = []

def must(old, new, label):
    global t
    if old not in t:
        log.append(f"MISS: {label}")
        return False
    t = t.replace(old, new, 1)
    log.append(f"OK: {label}")
    return True

# Show current values
for k in ["CONF_DRONE", "NEW_TARGET_CONF", "NEW_TARGET_NEED", "NEW_TARGET_DIST", "DRONE_MAX_M", "DRONE_MIN_M", "TRACK_SCORE", "DEPTHFLOW", "USE_DEDICATED"]:
    for i,l in enumerate(t.splitlines(),1):
        if k in l and ("=" in l or "def " in l) and i < 220:
            print(f"{i}|{l[:120]}")
