# -*- coding: utf-8 -*-
"""塔台运行时健康体检 2：状态字段 + 文件新鲜度 + 前端静态依赖"""
import json
import os
import re
import time
import urllib.request

BASE = "http://127.0.0.1:8080"
ROOT = r"E:\全家智能安全调度系统"
RT = os.path.join(ROOT, "tower_ui", "kinect_runtime")

st = json.load(urllib.request.urlopen(BASE + "/api/status", timeout=8))
print("=" * 66)
print("一、/api/status 逐字段体检")
print("=" * 66)
for grp in ("tower", "kinect", "kinect_frame", "uav", "uav_video", "vision"):
    v = st.get(grp)
    if isinstance(v, dict):
        bad = [k for k, x in v.items() if x is None or x == 0 and k in ("age_s",)]
        print(f"\n[{grp}]")
        for k, x in v.items():
            mark = ""
            if x is None:
                mark = "   <-- None（Windows 上取不到）"
            print(f"    {k:<16} = {json.dumps(x, ensure_ascii=False)[:80]}{mark}")
    else:
        print(f"\n[{grp}] = {json.dumps(v, ensure_ascii=False)[:100]}")
print(f"\n[ugv] = {json.dumps(st.get('ugv'), ensure_ascii=False)}")
print(f"[missions] 条数 = {len(st.get('missions', []))}")

print()
print("=" * 66)
print("二、kinect_runtime 文件新鲜度（现在 " + time.strftime("%H:%M:%S") + "）")
print("=" * 66)
for f in ("color.jpg", "depth_raw.bin", "depth_meta.json", "annotated.jpg",
          "state.json", "status.json", "motion.json"):
    p = os.path.join(RT, f)
    if not os.path.exists(p):
        print(f"    {f:<20} 缺失")
        continue
    age = time.time() - os.path.getmtime(p)
    flag = "OK " if age < 15 else ("旧 " if age < 120 else "陈旧!")
    print(f"    {f:<20} {os.path.getsize(p):>9,}B   {age:>7.1f}s 前  [{flag}]")
print(f"    kinect_off.flag      {'存在（Kinect 已关）' if os.path.exists(os.path.join(RT,'kinect_off.flag')) else '不存在（Kinect 供电中）'}")

print()
print("=" * 66)
print("三、前端静态依赖（离线可用性）")
print("=" * 66)
html_path = os.path.join(ROOT, "tower_ui", "web", "index.html")
html = open(html_path, encoding="utf-8").read()
print(f"    index.html {len(html):,} 字符 / {len(html.encode('utf-8')):,} 字节")
ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
cdn = set(re.findall(r'https?://([^/"]+)', html))
for h in sorted(cdn):
    print(f"    外部域名: {h}")
if not cdn:
    print("    无外部 CDN 依赖 -> 断网也能打开")
local = re.findall(r'(?:src|href)="(/[^"]+)"', html)
print(f"    站内绝对路径资源 {len(set(local))} 个: {sorted(set(local))[:10]}")

# 逐一探活站内资源
print()
for path in sorted(set(local)):
    path = path.split("?")[0]
    if path == "/":
        continue
    try:
        r = urllib.request.urlopen(BASE + path, timeout=5)
        print(f"    [PASS] {path}  {r.status} {r.headers.get('Content-Type','')} {len(r.read()):,}B")
    except Exception as e:
        print(f"    [FAIL] {path}  {type(e).__name__}: {e}")
