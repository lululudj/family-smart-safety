# -*- coding: utf-8 -*-
"""用画面里的已知尺度物体校准 physical_size_m 换算是否正确。

参照物：人的躯干（含手臂）宽度约 45~70cm；办公椅座面约 45~55cm。
如果换算错了 2 倍，人的宽度会跑出 1.4m 这种不可能的值。
"""
import json
import time
import urllib.request

EXPECT = {
    # label: (合理宽高范围 cm, 说明)
    "person": ((35, 90), "人体躯干宽度（应在 35~90cm）"),
    "chair": ((35, 80), "椅子座面宽度（应在 35~80cm）"),
    "tv": ((40, 140), "显示器/电视宽度（应在 40~140cm）"),
    "keyboard": ((25, 60), "键盘宽度（应在 25~60cm）"),
    "laptop": ((25, 50), "笔记本宽度（应在 25~50cm）"),
}


def get(path):
    with urllib.request.urlopen("http://127.0.0.1:8080" + path, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


print("等待画面中出现可校准的目标（最多 40 秒）...")
deadline = time.time() + 40
seen = {}
while time.time() < deadline:
    try:
        vis = get("/api/status")["vision"]
    except Exception:
        time.sleep(2)
        continue
    for o in vis.get("objects", []):
        if o.get("phys_m") and o["label"] not in seen:
            seen[o["label"]] = o
    if len(seen) >= 2:
        break
    time.sleep(2)

print()
print("=" * 68)
print("物理尺寸换算校准（由 2D 框 + Kinect 深度反算）")
print("=" * 68)
if not seen:
    print("  没取到带深度的目标。请站到 Kinect 前面再跑一次本脚本。")
else:
    all_ok = True
    for label, o in seen.items():
        w, h = [v * 100 for v in o["phys_m"]]
        line = f"  {label:<10} conf={o['conf']:.2f}  z={o['z']:.2f}m  →  {w:>6.1f} x {h:>6.1f} cm"
        if label in EXPECT:
            (lo, hi), desc = EXPECT[label]
            ok = lo <= max(w, h) <= hi
            all_ok = all_ok and ok
            line += f"   {'OK' if ok else 'X'}  ({desc})"
        print(line)
    print()
    print(f"  校准结论：{'换算正确' if all_ok else '仍有偏差，检查缩放系数'}")
