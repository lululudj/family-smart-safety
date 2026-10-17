import json, time, os, signal
from pathlib import Path
from datetime import datetime

root = Path(r"E:\全家智能安全调度系统")
out = root / "datasets" / "tello_kinect" / "raw" / "color"
color = root / "tower_ui" / "kinect_runtime" / "color.jpg"
state_path = root / "datasets" / "tello_kinect" / "raw" / "capture_run.json"
done_path = root / "datasets" / "tello_kinect" / "raw" / "capture_done.json"
log_path = root / "datasets" / "tello_kinect" / "raw" / "monitor.log"

def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

state = json.loads(state_path.read_text(encoding="utf-8-sig"))
pid = int(state["pid"])
target = int(state.get("target", 1500))
t0 = time.time()
last_count = -1
stall = 0

log(f"monitor start pid={pid} target={target}")

def count_jpg():
    return len(list(out.glob("*.jpg")))

def proc_alive(p):
    # Windows: tasklist
    import subprocess
    r = subprocess.run(["tasklist", "/FI", f"PID eq {p}"], capture_output=True, text=True)
    return str(p) in r.stdout and "python" in r.stdout.lower()

def kill(p):
    import subprocess
    subprocess.run(["taskkill", "/PID", str(p), "/F"], capture_output=True)

while True:
    n = count_jpg()
    alive = proc_alive(pid)
    # Kinect freshness
    try:
        age = time.time() - color.stat().st_mtime
    except OSError:
        age = 9999
    free_ok = True
    try:
        import shutil
        free_gb = shutil.disk_usage("E:\\").free / (1024**3)
        if free_gb < 2:
            free_ok = False
    except Exception:
        free_gb = -1

    if n != last_count:
        log(f"count={n} alive={alive} kinect_age={age:.1f}s free_gb={free_gb:.1f}")
        last_count = n
        stall = 0
    else:
        stall += 1

    # stop conditions
    reason = None
    if n >= target:
        reason = "reached_target"
    elif not alive:
        reason = "capture_exited"
    elif age > 30:
        reason = "kinect_stale"
    elif not free_ok:
        reason = "disk_low"
    elif stall >= 40 and n < target:
        # ~40*5s = 200s no growth
        reason = "count_stalled"

    if reason:
        if alive:
            kill(pid)
            time.sleep(1)
        final = count_jpg()
        elapsed = time.time() - t0
        result = {
            "reason": reason,
            "pid": pid,
            "final_count": final,
            "target": target,
            "elapsed_sec": round(elapsed, 1),
            "elapsed_min": round(elapsed / 60, 2),
            "kinect_age_s": round(age, 1),
            "free_gb": round(free_gb, 2) if free_gb >= 0 else None,
            "ended_at": datetime.now().isoformat(timespec="seconds"),
        }
        done_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"DONE {result}")
        break

    time.sleep(5)