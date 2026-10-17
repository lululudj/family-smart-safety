# -*- coding: utf-8 -*-
"""塔台前后端健康检查：把每个 API 真跑一遍，输出 PASS/FAIL。"""
import json
import socket
import sys
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8080"
TIMEOUT = 6


def req(method, path, body=None, timeout=TIMEOUT, raw=False):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            payload = resp.read()
            code = resp.status
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        payload = e.read()
        code = e.code
        ctype = e.headers.get("Content-Type", "") if e.headers else ""
    except Exception as e:
        return {"code": -1, "err": f"{type(e).__name__}: {e}", "ms": int((time.time() - t0) * 1000)}
    dt = int((time.time() - t0) * 1000)
    if raw or "json" not in ctype:
        return {"code": code, "bytes": len(payload), "ctype": ctype, "ms": dt, "rawhead": payload[:80]}
    try:
        return {"code": code, "json": json.loads(payload.decode("utf-8", "replace")), "bytes": len(payload), "ms": dt}
    except Exception as e:
        return {"code": code, "err": f"json parse: {e}", "bytes": len(payload), "ms": dt}


def show(title, res, limit=420):
    code = res.get("code")
    flag = "PASS" if code == 200 else ("FAIL" if code == -1 else f"HTTP {code}")
    line = f"[{flag}] {title}  ({res.get('ms')}ms"
    if "bytes" in res:
        line += f", {res['bytes']}B)"
    else:
        line += ")"
    print(line)
    if "err" in res:
        print("       err:", res["err"][:limit])
    elif "json" in res:
        s = json.dumps(res["json"], ensure_ascii=False)
        print("       ->", s[:limit])
    elif "rawhead" in res:
        print("       ->", res.get("ctype"), res["rawhead"][:60])
    print()


print("=" * 70)
print("Ganglion 塔台健康检查  ", time.strftime("%Y-%m-%d %H:%M:%S"))
print("=" * 70)

# 0. 端口
for port in (8080, 8091, 11111, 8889):
    s = socket.socket()
    s.settimeout(1)
    r = s.connect_ex(("127.0.0.1", port))
    s.close()
    print(f"端口 {port:>5}: {'LISTEN' if r == 0 else 'closed/unreachable'}")
print()

print("--- 前端 ---")
show("GET /  (主页 index.html)", req("GET", "/", raw=True))
print("--- 后端核心 ---")
show("GET /api/status", req("GET", "/api/status"))
show("GET /api/kinect/power", req("GET", "/api/kinect/power"))
show("POST /api/kinect/frame", req("POST", "/api/kinect/frame"))
show("GET /api/tts/voices", req("GET", "/api/tts/voices"))
print("--- 无人机 ---")
show("POST /api/uav/cmd battery?", req("POST", "/api/uav/cmd", {"cmd": "battery?"}, timeout=12))
print("--- LLM 管家 ---")
show("POST /api/butler/chat", req("POST", "/api/butler/chat", {"message": "现在家里什么情况"}, timeout=30))
print("--- 视觉 ---")
try:
    with urllib.request.urlopen(BASE + "/kinect/live.mjpg", timeout=4) as f:
        head = f.read(2048)
        print(f"[PASS] GET /kinect/live.mjpg  前 2KB 含边界: {b'boundary' in head or b'--' in head}, {len(head)}B")
except Exception as e:
    print(f"[FAIL] GET /kinect/live.mjpg  {type(e).__name__}: {e}")
print()
print("=" * 70)
