# -*- coding: utf-8 -*-
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

# 1) thresholds
must("CONF_DRONE = 0.008\n",
     "CONF_DRONE_WORLD = 0.05\n"
     "CONF_DRONE_DEDICATED = 0.15\n"
     "CONF_DRONE = CONF_DRONE_DEDICATED  # runtime: load_model 按 TELLO_MODEL 切换\n"
     "DRONE_MIN_AREA_PX = 700           # 最小框面积，滤桌面杂物小斑\n",
     "CONF_DRONE dual + min area")

must("NEW_TARGET_DIST_M = 0.90\n"
     "NEW_TARGET_NEED = 2\n"
     "NEW_TARGET_CONF = 0.05\n",
     "NEW_TARGET_DIST_M = 0.70\n"
     "NEW_TARGET_NEED = 3\n"
     "NEW_TARGET_CONF = 0.15\n",
     "NEW_TARGET stricter")

must("DRONE_MAX_M = 0.50\n", "DRONE_MAX_M = 0.35\n", "DRONE_MAX_M 0.35")

must("TRACK_SCORE_WEAK_MIN = CONF_DRONE * 0.75\n",
     "TRACK_SCORE_WEAK_MIN = CONF_DRONE  # 弱检不得低于验收线\n",
     "WEAK_MIN = CONF_DRONE")

# 2) load_model sets CONF_DRONE
old_load_ded = '''    if want in ("dedicated", "hybrid") and os.path.isfile(ded_wt):
        model = YOLO(ded_wt)
        USE_DEDICATED_TELLO = True
        print(f"[model] dedicated tello -> {ded_wt}")
        return model
    USE_DEDICATED_TELLO = False
    if os.path.exists(MODEL_NAME):
        model = YOLO(MODEL_NAME)
        if hasattr(model, "set_classes"):
            model.set_classes(WORLD_CLASSES)
        note = ("（存在专用权重但默认不启用；要启用设 TELLO_MODEL=dedicated）"
                if os.path.isfile(TELLO_DEDICATED_WT) else "")
        print(f"[model] YOLO-World -> {MODEL_NAME}{note}")
        return model'''
new_load_ded = '''    global CONF_DRONE, TRACK_SCORE_WEAK_MIN
    if want in ("dedicated", "hybrid") and os.path.isfile(ded_wt):
        model = YOLO(ded_wt)
        USE_DEDICATED_TELLO = True
        CONF_DRONE = CONF_DRONE_DEDICATED
        TRACK_SCORE_WEAK_MIN = CONF_DRONE
        print(f"[model] dedicated tello -> {ded_wt}  CONF_DRONE={CONF_DRONE}")
        return model
    USE_DEDICATED_TELLO = False
    if os.path.exists(MODEL_NAME):
        model = YOLO(MODEL_NAME)
        if hasattr(model, "set_classes"):
            model.set_classes(WORLD_CLASSES)
        CONF_DRONE = CONF_DRONE_WORLD
        TRACK_SCORE_WEAK_MIN = CONF_DRONE
        note = ("（存在专用权重但默认不启用；要启用设 TELLO_MODEL=dedicated）"
                if os.path.isfile(TELLO_DEDICATED_WT) else "")
        print(f"[model] YOLO-World -> {MODEL_NAME}{note}  CONF_DRONE={CONF_DRONE}")
        return model'''
must(old_load_ded, new_load_ded, "load_model sets CONF_DRONE")

# 3) _boxes_from_result: remove 0.75 neighbor looser; add min area; require CONF_DRONE
# Find the thr block
old_thr = '''            # 模型侧已用 CONF_DRONE 出框；人用 CONF_GENERAL；邻域内 drone 可松到 0.75x
            if not is_drone:
                if conf < CONF_GENERAL:
                    continue
            elif conf < CONF_DRONE * 0.75:
                continue
            bx1, by1, bx2, by2 = (float(v) for v in b.xyxy[0])
            x1, y1, x2, y2 = bx1 + ox, by1 + oy, bx2 + ox, by2 + oy
            if is_drone:
                if (x2 - x1) * (y2 - y1) > 180000:
                    continue
                near = _near_last_box({"box": (x1, y1, x2, y2)})
                if (not near) and conf < CONF_DRONE:
                    continue'''
new_thr = '''            # 验收线：drone 必须 conf>=CONF_DRONE（dedicated≈0.15 / world≈0.05）
            if not is_drone:
                if conf < CONF_GENERAL:
                    continue
            elif conf < CONF_DRONE:
                continue
            bx1, by1, bx2, by2 = (float(v) for v in b.xyxy[0])
            x1, y1, x2, y2 = bx1 + ox, by1 + oy, bx2 + ox, by2 + oy
            if is_drone:
                area = (x2 - x1) * (y2 - y1)
                if area > 180000:  # 巨框（像人）
                    continue
                if area < DRONE_MIN_AREA_PX:  # 桌面杂物小斑
                    continue'''
must(old_thr, new_thr, "boxes thr + min area, no 0.75 looser")

# 4) second pass conf already uses CONF_DRONE - good. Also add min area there if needed.
old_sp_conf = '''            if conf < CONF_DRONE:
                continue
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            # 拒绝盖住人的巨框（常见把 person 误成 tello）
            if (x2 - x1) * (y2 - y1) > 0.22 * frame_area:
                continue'''
new_sp_conf = '''            if conf < CONF_DRONE:
                continue
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            area = (x2 - x1) * (y2 - y1)
            if area > 0.22 * frame_area:
                continue
            if area < DRONE_MIN_AREA_PX:
                continue'''
must(old_sp_conf, new_sp_conf, "second_pass min area")

# 5) update_track_score: only verified conf>=CONF_DRONE gains; depthflow only if locked
old_score_logic = '''    best = None
    hit_kind = None
    if yolo_verified:
        best = max(yolo_verified, key=lambda o: o.get("conf") or 0)
        hit_kind = "yolo"
    elif depthflow_hits:
        best = depthflow_hits[0]
        hit_kind = "depthflow"
    elif near_weak and _track_score["score"] >= TRACK_SCORE_LOCK * 0.5:
        best = max(near_weak, key=lambda o: o.get("conf") or 0)
        hit_kind = "weak"
    elif near_weak and any(_near_last_box(o) for o in near_weak):
        best = max(near_weak, key=lambda o: o.get("conf") or 0)
        hit_kind = "weak"

    sc = float(_track_score.get("score") or 0.0)
    if hit_kind == "yolo" and best is not None:
        conf = float(best.get("conf") or 0)
        gain = TRACK_SCORE_GAIN + conf * TRACK_SCORE_CONF_SCALE
        sc = min(TRACK_SCORE_MAX, sc + gain)
        _track_score["hits"] = int(_track_score.get("hits") or 0) + 1
        _track_score["misses"] = 0
    elif hit_kind == "depthflow" and best is not None:
        sc = min(TRACK_SCORE_MAX, sc + DEPTHFLOW_SCORE_GAIN)
        # 续跟成功：轻微衰减/加分，保持锁定更久
        sc = max(sc, min(TRACK_SCORE_MAX, sc))  # no-op keep
        _track_score["hits"] = int(_track_score.get("hits") or 0) + 1
        _track_score["misses"] = 0
    elif hit_kind == "weak" and best is not None:
        conf = float(best.get("conf") or 0)
        gain = TRACK_SCORE_GAIN * 0.6 + conf * TRACK_SCORE_CONF_SCALE
        sc = min(TRACK_SCORE_MAX, sc + gain)
        _track_score["hits"] = int(_track_score.get("hits") or 0) + 1
        _track_score["misses"] = 0
    else:
        decay = TRACK_SCORE_DECAY
        if _flow_state.get("last_fail"):
            decay = DEPTHFLOW_FAIL_DECAY
        elif _flow_state.get("active"):
            decay = DEPTHFLOW_DECAY
        sc = max(0.0, sc - decay)
        _track_score["misses"] = int(_track_score.get("misses") or 0) + 1'''

new_score_logic = '''    # 仅 conf>=CONF_DRONE 的 verified YOLO 可加分；弱检不加分；depthflow 仅已锁定时可维持
    best = None
    hit_kind = None
    locked_now = float(_track_score.get("score") or 0) >= TRACK_SCORE_LOCK
    strong = [o for o in yolo_verified if (o.get("conf") or 0) >= CONF_DRONE]
    if strong:
        best = max(strong, key=lambda o: o.get("conf") or 0)
        hit_kind = "yolo"
    elif locked_now and depthflow_hits:
        best = depthflow_hits[0]
        hit_kind = "depthflow"
    # 未锁定：忽略 near_weak / 低分，避免桌面杂物把 score 抬到 LOCK

    sc = float(_track_score.get("score") or 0.0)
    if hit_kind == "yolo" and best is not None:
        conf = float(best.get("conf") or 0)
        gain = TRACK_SCORE_GAIN + conf * TRACK_SCORE_CONF_SCALE
        sc = min(TRACK_SCORE_MAX, sc + gain)
        _track_score["hits"] = int(_track_score.get("hits") or 0) + 1
        _track_score["misses"] = 0
    elif hit_kind == "depthflow" and best is not None:
        # 已锁定才允许：小幅加分，禁止从 0 拉到 LOCK
        sc = min(TRACK_SCORE_MAX, sc + DEPTHFLOW_SCORE_GAIN)
        _track_score["hits"] = int(_track_score.get("hits") or 0) + 1
        _track_score["misses"] = 0
    else:
        decay = TRACK_SCORE_DECAY
        if _flow_state.get("last_fail"):
            decay = DEPTHFLOW_FAIL_DECAY
        elif locked_now and _flow_state.get("active"):
            decay = DEPTHFLOW_DECAY
        sc = max(0.0, sc - decay)
        _track_score["misses"] = int(_track_score.get("misses") or 0) + 1'''
must(old_score_logic, new_score_logic, "score: only strong verified; depthflow if locked")

# 6) maybe_attach_depthflow: off until locked
old_df_gate = '''    # no YOLO drone → try depthflow if track active & within window
    if not _flow_state.get("active"):
        return objects
    yolo_age = now - float(_flow_state.get("yolo_ts") or 0)
    if yolo_age > DEPTHFLOW_MAX_S + 0.15:
        _clear_flow_track("yolo-timeout")
        return objects
    if float(_track_score.get("score") or 0) <= TRACK_SCORE_DROP:
        _clear_flow_track("score-drop")
        return objects'''
new_df_gate = '''    # no YOLO drone → depthflow 仅在已 LOCK 后启用（禁止从未锁定用光流刷分）
    if not _flow_state.get("active"):
        return objects
    if float(_track_score.get("score") or 0) < TRACK_SCORE_LOCK:
        return objects
    yolo_age = now - float(_flow_state.get("yolo_ts") or 0)
    if yolo_age > DEPTHFLOW_MAX_S + 0.15:
        _clear_flow_track("yolo-timeout")
        return objects
    if float(_track_score.get("score") or 0) <= TRACK_SCORE_DROP:
        _clear_flow_track("score-drop")
        return objects'''
must(old_df_gate, new_df_gate, "depthflow only when LOCKED")

# Also seed flow only from strong conf
old_seed = '''    if yolo_good and color is not None:
        best = max(yolo_good, key=lambda o: o.get("conf") or 0)
        _seed_flow_track(best, color)
        return objects'''
new_seed = '''    if yolo_good and color is not None:
        strong = [o for o in yolo_good if (o.get("conf") or 0) >= CONF_DRONE]
        if strong:
            best = max(strong, key=lambda o: o.get("conf") or 0)
            _seed_flow_track(best, color)
        return objects'''
must(old_seed, new_seed, "seed flow only strong YOLO")

# 7) filter: drop drones below CONF_DRONE even if verified somehow
old_filt = '''        if o.get("verified") or o.get("det_src") == "depthflow" or o.get("track_mode") == "depthflow":
            o2 = dict(o)
            o2["track_score"] = round(score, 3)
            o2["track_locked"] = locked
            out.append(o2)
            continue
        conf = o.get("conf") or 0
        if conf < TRACK_SCORE_WEAK_MIN:
            continue
        if locked or _near_last_box(o) or score >= TRACK_SCORE_LOCK * 0.5:
            o2 = dict(o)
            o2["track_score"] = round(score, 3)
            o2["track_locked"] = locked
            out.append(o2)
    return out'''
new_filt = '''        # depthflow：仅锁定后保留
        if o.get("det_src") == "depthflow" or o.get("track_mode") == "depthflow":
            if locked:
                o2 = dict(o)
                o2["track_score"] = round(score, 3)
                o2["track_locked"] = locked
                out.append(o2)
            continue
        conf = o.get("conf") or 0
        # 未 verified 或不达标 conf：不进列表（也就不画橙框）
        if not o.get("verified") or conf < CONF_DRONE:
            continue
        o2 = dict(o)
        o2["track_score"] = round(score, 3)
        o2["track_locked"] = locked
        out.append(o2)
    return out'''
must(old_filt, new_filt, "filter: only verified>=CONF_DRONE; depthflow if locked")

# 8) annotate: skip drawing low-quality drones
old_ann_loop = '''    for o in objects:
        x1, y1, x2, y2 = (int(v) for v in o["box"])
        has_depth = o.get("z") is not None
        is_drone = o.get("drone")
        verified = o.get("verified")
        is_held = bool(o.get("held"))

        if is_drone:
            box_color = DRONE_COLOR
            thickness = 4 if verified and not is_held else 2
        else:'''
new_ann_loop = '''    for o in objects:
        is_drone = o.get("drone")
        verified = o.get("verified")
        is_held = bool(o.get("held"))
        is_flow = (o.get("det_src") == "depthflow" or o.get("track_mode") == "depthflow")
        # 宁可漏检：未 verified / conf 不够的候选不画橙框（depthflow 青色除外）
        if is_drone and not is_flow and not is_held:
            if (not verified) or (o.get("conf") or 0) < CONF_DRONE:
                continue
        x1, y1, x2, y2 = (int(v) for v in o["box"])
        has_depth = o.get("z") is not None

        if is_drone:
            box_color = DRONE_COLOR
            thickness = 4 if verified and not is_held else 2
        else:'''
must(old_ann_loop, new_ann_loop, "annotate skip low-quality drones")

# Fix duplicate is_flow later in annotate - there will be two is_flow assignments
# Check if "is_flow = (o.get" appears twice after patch
print("==== LOG ====")
for x in log:
    print(x)

# dedupe is_flow in annotate body if double-defined
count_flow = t.count("is_flow = (o.get(\"det_src\") == \"depthflow\"")
print("is_flow assigns", count_flow)
if count_flow >= 2:
    # remove the second one inside annotate (the old line after locked=)
    old_dup = '''        locked = bool(o.get("track_locked") or _track_score.get("locked"))
        is_flow = (o.get("det_src") == "depthflow" or o.get("track_mode") == "depthflow")
        if is_flow and is_drone:'''
    new_dup = '''        locked = bool(o.get("track_locked") or _track_score.get("locked"))
        if is_flow and is_drone:'''
    if old_dup in t:
        t = t.replace(old_dup, new_dup, 1)
        log.append("OK: dedupe is_flow")
        print("OK dedupe is_flow")

checks = {
    "CONF_DRONE_DEDICATED = 0.15": "CONF_DRONE_DEDICATED = 0.15" in t,
    "CONF_DRONE_WORLD = 0.05": "CONF_DRONE_WORLD = 0.05" in t,
    "no CONF_DRONE = 0.008": "CONF_DRONE = 0.008" not in t,
    "NEW_TARGET_CONF = 0.15": "NEW_TARGET_CONF = 0.15" in t,
    "NEW_TARGET_NEED = 3": "NEW_TARGET_NEED = 3" in t,
    "DRONE_MAX_M = 0.35": "DRONE_MAX_M = 0.35" in t,
    "DRONE_MIN_AREA_PX": "DRONE_MIN_AREA_PX" in t,
    "depthflow LOCK gate": "depthflow 仅在已 LOCK" in t or "float(_track_score.get(\"score\") or 0) < TRACK_SCORE_LOCK" in t,
}
print("==== CHECKS ====")
ok = True
for k,v in checks.items():
    print(("OK" if v else "FAIL"), k)
    ok &= v
hard = [x for x in log if x.startswith("MISS")]
if hard or not ok:
    print("ABORT", hard)
else:
    p.write_text(t, encoding="utf-8", newline="\n")
    print("WROTE", p.stat().st_size)
