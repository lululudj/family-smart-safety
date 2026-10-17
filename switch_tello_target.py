# -*- coding: utf-8 -*-
"""一键切换塔台的 Tello 目标：真机 <-> 本机模拟器。

用法：
    python switch_tello_target.py mock   # 指向本机模拟器（联调用）
    python switch_tello_target.py real   # 指向真机 192.168.10.1
切换后需要重启塔台（keeper 会在 ~15 秒内自动拉起）。
"""
import json
import os
import sys

CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "tower_ui", "tower_config.json")

mode = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
if mode not in ("mock", "real"):
    print("用法: python switch_tello_target.py [mock|real]")
    try:
        cur = json.load(open(CFG, encoding="utf-8"))
        print(f"当前 tello_ip = {cur.get('tello_ip')}")
    except Exception:
        pass
    sys.exit(1)

cfg = {}
try:
    with open(CFG, "r", encoding="utf-8") as f:
        cfg = json.load(f)
except Exception:
    pass

if mode == "mock":
    cfg["tello_ip"] = "127.0.0.1"
    cfg["tello_ssid"] = "TELLO-MOCK"
else:
    cfg["tello_ip"] = "192.168.10.1"
    cfg["tello_ssid"] = "TELLO-5896C7"

with open(CFG, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

print(f"已切换到 {mode} 模式: tello_ip = {cfg['tello_ip']}")
print("等待 keeper 自动重启塔台（约 15 秒）后生效；也可手动重启塔台立即生效。")
