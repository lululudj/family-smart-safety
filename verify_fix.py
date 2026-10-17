# -*- coding: utf-8 -*-
"""验证本次两处修复是否生效"""
import json
import time
import urllib.request


def get(path):
    with urllib.request.urlopen("http://127.0.0.1:8080" + path, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, payload):
    req = urllib.request.Request(
        "http://127.0.0.1:8080" + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


print("=" * 64)
print("一、Tello 链路探测修复验证")
print("=" * 64)
st = get("/api/status")
uav = st["uav"]
for k, v in uav.items():
    print(f"    {k:<12} = {json.dumps(v, ensure_ascii=False)}")
keys = ("hint", "apipa")
ok1 = all(k in uav for k in keys)
print(f"\n  -> 新字段(hint/apipa)已出现: {ok1}")
if uav.get("hint"):
    print(f"  -> 前端可读的离线原因: {uav['hint']}")
print(f"  -> Kinect 仍健康: connected={st['kinect']['connected']}, "
      f"frame {st['kinect_frame'].get('age_s')}s 前")

print()
print("=" * 64)
print("二、无人机检测字段透传验证（模拟一台 Tello 上报）")
print("=" * 64)
r = post("/api/vision/detections", {
    "source": "selftest",
    "objects": [{
        "label": "drone", "conf": 0.42, "x": 0.12, "y": -0.30, "z": 1.55,
        "drone": True, "verified": True, "phys_m": [0.31, 0.29],
    }],
    "drone_mode": True,
})
print(f"   上报回执: {r}")
# 真实识别器每 2 秒上报一次会覆盖 mock，所以立刻抢读几轮
hit = None
for _ in range(8):
    vis = get("/api/status")["vision"]
    if vis.get("source") == "selftest" and vis["objects"]:
        hit = vis
        break
    time.sleep(0.15)
if hit:
    o = hit["objects"][0]
    print(f"   抢到自检数据: {json.dumps(o, ensure_ascii=False)}")
    ok2 = o.get("drone") is True and o.get("verified") is True and bool(o.get("phys_m"))
else:
    o = {}
    ok2 = False
    print("   未抢到自检数据（被实时识别器覆盖）")
print(f"  -> drone/verified/phys_m 透传成功: {bool(ok2)}")
if o.get("phys_m"):
    print(f"  -> 反算物理尺寸 {o['phys_m'][0]*100:.0f}x{o['phys_m'][1]*100:.0f}cm"
          f"（Tello 含桨约 33x33cm，落在 6~45cm 校验窗内）")

# 顺带看真实识别结果里的物理尺寸，验证 physical_size_m 换算是否靠谱
vis = get("/api/status")["vision"]
reals = [(x["label"], x.get("phys_m")) for x in vis.get("objects", []) if x.get("phys_m")]
if reals:
    print("\n  实时画面的物理尺寸反算（检验换算精度）：")
    for label, pm in reals:
        print(f"    {label:<12} {(pm[0]*100):>5.0f} x {(pm[1]*100):>5.0f} cm")

print()
print("=" * 64)
print("三、结论")
print("=" * 64)
print(f"  链路探测修复 : {'PASS' if ok1 else 'FAIL'}")
print(f"  无人机字段   : {'PASS' if ok2 else 'FAIL'}")
