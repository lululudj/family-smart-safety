#!/usr/bin/env python3
"""kinect_yolo3d —— Kinect 彩色 + 原始深度 → 物体的三维坐标

链路：
    Kinect 桥接（Windows）
        ├─ color.jpg        1920×1080 彩色
        ├─ depth_raw.bin    960×540   uint16 原始深度（毫米，已对齐到彩色网格）
        └─ depth_meta.json  宽高 / 单位 / 彩色内参 fx,fy,cx,cy
              ↓
    YOLO 在 color.jpg 上找框
              ↓
    把框映射到深度网格，取框内有效深度的中位数
              ↓
    针孔模型反投影 → (X, Y, Z) 米（相机坐标系：X 右, Y 下, Z 前）
              ↓
    POST 塔台 /api/vision/detections

变化检测（A 档）：桥接程序只在「检测到变化」或「心跳超时」时才写大帧，
本脚本相应只在「出现新帧」时跑一次 YOLO，同时每 2 秒上报一次变化度，
让网页能实时看到「静止 / 轻微 / 明显」。

用法：
    python kinect_yolo3d.py               # 持续运行，每 2 秒上报一次
    python kinect_yolo3d.py --interval 1  # 自定义上报间隔（秒）
    python kinect_yolo3d.py --probe       # 只处理一次并打印结果，不上报（离线验证用）
前提：Kinect 桥接程序在跑（产生 color.jpg / depth_raw.bin / motion.json）
"""
import argparse
import gc
import json
import os
import queue
import socket
import sys
import threading
import time
import urllib.request

import cv2
import numpy as np
import requests
import torch
from ultralytics import YOLO

# ---------------------------------------------------------------- 配置
KINECT_DIR = os.environ.get(
    "KINECT_DIR", r"E:\全家智能安全调度系统\tower_ui\kinect_runtime"
)
TOWER = os.environ.get("TOWER", "http://127.0.0.1:8080")  # 塔台已迁到本机（香橙派已退役）
REPORT = f"{TOWER}/api/vision/detections"  # 低频检测结果上报（每 2 秒一次）

# 高频位姿推送（UDP）：悬停控制器要闭环，位姿必须新鲜、低延迟。
# 走 UDP 而非 HTTP —— 本地 Flask 不支持 keep-alive，推理帧率下每帧新建 TCP 连接
# 会堆 TIME_WAIT 耗尽端口（历史上 12fps 时踩过，直接拖垮 USB）。UDP 无连接、零残留。
POSE_PORT = int(os.environ.get("POSE_PORT", "8898"))    # 塔台（显示 / 监控）
HOVER_PORT = int(os.environ.get("HOVER_PORT", "8899"))  # 悬停控制器（闭环）
POSE_TARGETS = (("127.0.0.1", POSE_PORT), ("127.0.0.1", HOVER_PORT))
# 有效工作距离窗口（米）：实测 YOLO-World 会把远处的白色静物（窗帘/枕头一类）
# 稳定误判成 toy drone。窗口别设得太紧 —— 实测 Tello 被放到 2.3m 处就会被挡掉。
# 3.0m 兼顾"室内巡逻够用"和"挡住更远的误检"，近处误检由 conf+尺寸+跟踪兜底。
POSE_Z_MIN = float(os.environ.get("POSE_Z_MIN", "0.20"))
POSE_Z_MAX = float(os.environ.get("POSE_Z_MAX", "3.0"))
_pose_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
# 位姿跟踪状态：多候选时用来挑"延续上一帧"的那个，避免在真 Tello 和误检物体间横跳
_last_pose = {"x": None, "y": None, "z": None, "ts": 0.0}
_last_box = {"box": None, "ts": 0.0, "vx": 0.0, "vy": 0.0}  # xyxy + px/s for ROI only
_pose_dbg_ts = 0.0
ROI_SCALE = 3.5
ROI_MIN_PX = 320
ROI_MAX_AGE = 2.0
ROI_IMGSZ = 960  # higher res on crop; full-frame stays 640
FULL_EVERY_N = 3
NEW_TARGET_DIST_M = 0.70
NEW_TARGET_NEED = 3
NEW_TARGET_CONF = 0.15
NEIGHBOR_PX = 140  # same-target if box center within this of last/extrapolated
_infer_i = 0
_pending_new = {"key": None, "hits": 0}


def _dbg_no_pose(objects):
    """没有可用位姿时，说清是被哪条规则挡掉的（限频输出，便于排障）。"""
    global _pose_dbg_ts
    now = time.time()
    if now - _pose_dbg_ts < 2.0:
        return
    _pose_dbg_ts = now
    drones = [o for o in objects if o.get("drone")]
    # 调试：只要没有可用位姿就存一张原始帧（限频同上），
    # 便于事后看"飞行中的 Tello 到底长什么样"
    try:
        frame = read_color()
        if frame is not None:
            # 用 imencode + tofile，绕开 OpenCV imwrite 不支持中文路径的问题
            ok, buf = cv2.imencode(".jpg", frame)
            if ok:
                buf.tofile(os.path.join(KINECT_DIR, "debug_nodrone.jpg"))
    except Exception:
        pass
    if not drones:
        labels = [o.get("label") for o in objects][:8]
        print(f"[pose] 本帧无 drone 检测（共 {len(objects)} 个对象: {labels}）")
        return
    parts = []
    for o in drones:
        why = []
        if not o.get("verified"):
            why.append("尺寸不过")
        if o.get("z") is None:
            why.append("无深度")
        elif not (POSE_Z_MIN <= o["z"] <= POSE_Z_MAX):
            why.append(f"z={o['z']:.2f}超窗")
        if o.get("held"):
            why.append("hold回灌")
        parts.append(f"{o.get('label')}/conf{o.get('conf')}/{' '.join(why) or '可用'}")
    print(f"[pose] 无可用位姿 <- " + " ; ".join(parts))

# YOLO-World 开放词汇模型：可自定义类别（COCO 80 类里没有"无人机"，所以换它）
_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_NAME = os.path.join(_ROOT_DIR, "yolov8s-worldv2.pt")
# Dedicated Tello fine-tune weight (preferred); fall back to YOLO-World if missing
TELLO_DEDICATED_WT = os.path.join(
    _ROOT_DIR, "datasets", "tello_kinect", "weights", "tello_yolov8n.pt"
)
USE_DEDICATED_TELLO = False  # set True in load_model() when dedicated wt loads
# 检测类别：家里常见物体 + 无人机。
# prompt 调优实测（同一张无人机图，conf 分数）：
#   "toy drone" 0.43  >>  "drone" 0.16  >  "quadcopter drone" 0.11
#   > "组合4同义词" 0.08  >  "flying camera drone" 0.05  >  "四桨描述" 0.03
# 结论：CLIP 文本嵌入偏爱口语化常见词，"toy drone"（Tello 本来就是玩具定位）
# 比抽象技术词强 3~6 倍。保留 "drone" 做兜底（真机外观不像玩具时），
# 两者识别后统一归一为 drone（见 DRONE_ALIAS）。
# 只要人+无人机；词表精简，避免 YOLO-World 长列表稀释 drone 分数
WORLD_CLASSES = [
    "person",
    "toy drone", "drone", "white quadcopter", "flying drone", "handheld drone",
]
KEEP_LABELS = {"person", "drone"}
PERSON_ONLY_CLASSES = ["person"]
DRONE_WORLD_CLASSES = [
    "toy drone", "drone", "white quadcopter", "flying drone", "handheld drone",
]
PERSON_ALIAS = {
    "person": "person", "people": "person", "human": "person",
}
DRONE_ALIAS = {
    "toy drone": "drone", "drone": "drone",
    "tello": "drone", "dji tello": "drone", "DJI Tello": "drone",
    "white quadcopter": "drone", "small drone": "drone",
    "mini drone": "drone", "quadcopter": "drone",
    "quadcopter drone": "drone", "white toy drone": "drone",
    "uav": "drone",
    "white drone": "drone", "small white drone": "drone",
    "handheld drone": "drone",
    "flying drone": "drone", "hovering drone": "drone",
    "drone in flight": "drone", "quadcopter in the air": "drone",
    "white drone flying": "drone",
}
# 易把 Tello 误判成这些类：尺寸窗内可提升为无人机候选
PROMOTE_LABELS = set()  # disabled: bottle/phone promote off; only person+drone
PROMOTE_MAX_ASPECT = 5.0  # 略放宽，减少手持角度漏提升
CONF_PROMOTE = 0.12  # bottle/phone 等提升候选的 conf 门槛（低于普通物体）

# 置信度门槛：drone 在开放词汇下天然偏低，单独放宽；常规物体保持严格控噪
CONF_GENERAL = 0.35
# 无人机门限。实测 YOLO-World 的 "toy drone" 对真实 Tello 响应本就非常弱：
#   地面静止 conf 0.06~0.21；升空飞行时只剩 ~0.006 —— 差一个数量级。
# 原因：飞行中又小又白，背景是亮白瓷砖，对比度极低，再叠上桨叶运动模糊。
# 注意别为了"飞起来也能检出"把门限压到 0.005 —— 那样选中的是显示器一类
# 误检（conf 0.006~0.012），比真 Tello 还高，控制器会转头去追它。
# 当前 0.02：地面稳检；飞行中的检出依赖改善对比度（深色背景）或重训专属权重。
CONF_DRONE_WORLD = 0.05
CONF_DRONE_DEDICATED = 0.15
CONF_DRONE = CONF_DRONE_DEDICATED  # runtime: load_model 按 TELLO_MODEL 切换
DRONE_MIN_AREA_PX = 700           # 最小框面积，滤桌面杂物小斑
# ---- 轨迹加分跟踪（只影响显示/弱检接纳；push_pose 仍只要本帧真实检测）----
TRACK_SCORE_MAX = 1.0
TRACK_SCORE_GAIN = 0.28          # 每帧命中基础加分
TRACK_SCORE_CONF_SCALE = 0.9     # + conf * scale
TRACK_SCORE_DECAY = 0.14         # 未检到每帧衰减
TRACK_SCORE_LOCK = 0.42          # >= 实线橙框 + 允许采纳邻域弱检
TRACK_SCORE_DROP = 0.05          # 低于此丢弃跟踪
TRACK_SCORE_WEAK_MIN = CONF_DRONE  # 弱检不得低于验收线
# ---- 彩色光流 + 深度团块续跟（YOLO 短暂丢检补洞）----
# MAX_S 略大于 hover LOST_ABORT（0.8s），短丢检仍能在降落前把真实深度喂进 UDP。
# 不要把 LOST_ABORT 拉长去掩盖假续跟；窗口够用即可。
DEPTHFLOW_MAX_S = float(os.environ.get("DEPTHFLOW_MAX_S", "1.2"))
DEPTHFLOW_DZ_M = float(os.environ.get("DEPTHFLOW_DZ_M", "0.50"))  # 上一 z ±Δz 搜索带
# 搜索带 ±0.50m 已经罩得住身后的墙。z 门必须更紧，否则带内粘连团块过得了 0.60m。
DEPTHFLOW_Z_JUMP_M = float(os.environ.get("DEPTHFLOW_Z_JUMP_M", "0.35"))
DEPTHFLOW_JUMP_M = float(os.environ.get("DEPTHFLOW_JUMP_M", "0.60"))  # 三维，对齐 hover JUMP_MAX
DEPTHFLOW_MIN_PTS = 24         # 有效深度点数下限
DEPTHFLOW_SCORE_GAIN = 0.10    # 续跟成功小幅加分（成功帧不衰减）
DEPTHFLOW_DECAY = 0.07         # 已锁定且光流仍活跃、但本帧没续跟上时的慢衰减
DEPTHFLOW_FAIL_DECAY = 0.20    # 团块/跳变/尺寸失败时加速衰减
DEPTHFLOW_COLOR = (255, 220, 40)  # BGR 青黄虚线，区别橙框 YOLO
_flow_state = {
    "prev_gray": None, "box": None, "z": None, "phys": None,
    "yolo_ts": 0.0, "flow_start": 0.0, "active": False,
    "last_fail": False, "lock_hold_until": 0.0,
}
_track_score = {"score": 0.0, "ts": 0.0, "hits": 0, "misses": 0, "locked": False}
_track_score_log_ts = 0.0
# Tello 物理尺寸（米）：机身 98×92.5×41mm；展开含桨叶约 328×328mm。
# 飞行时视觉框接近含桨尺寸，落地静止时接近机身 → 校验窗口取 6~45cm 留足余量。
DRONE_MIN_M = 0.05
DRONE_MAX_M = 0.35
DRONE_COLOR = (60, 150, 255)  # BGR 橙色：无人机专用高亮（区别于薄荷绿常规框）
INTERVAL = 0.2        # 桥接写帧节流（保留参考值，实际节奏由桥接决定）
REPORT_INTERVAL = 1.0  # 上报间隔（秒）——加快视觉位姿刷新
DRONE_HOLD_S = 0.5     # verified 丢失后仍保持位姿的秒数（抗闪断）
DRONE_HOLD_MAX_DIST = 180.0  # 与上一帧中心距离超过此像素则视为新目标，不续 hold
DEPTH_MIN_M = 0.5     # 有效深度下限（米）——Kinect v2 规范口径是 0.5m 起，近于此全是噪声
DEPTH_MAX_M = 8.0     # 有效深度上限（米）——过滤窗外远景
BOX_INNER = 0.6       # 只用框中心 60% 区域取深度，避开人物边缘串到背景
MINT = (183, 214, 77)  # 识别框颜色（BGR）——和内网页薄荷绿一致
GRAY = (150, 150, 150)  # 框内无有效深度时的框色


def load_model() -> YOLO:
    """返回主模型。

    TELLO_MODEL:
      world      — 默认：YOLO-World（person+精简 drone 词）
      dedicated  — 专用 YOLOv8n 检 drone；另载 World 仅 person（见 load_person_model）
      hybrid     — 同 dedicated（双模型）
    专用权重 mAP 差时不要默认切；由环境变量显式开启。
    """
    global USE_DEDICATED_TELLO
    want = os.environ.get("TELLO_MODEL", "world").strip().lower()
    score_wt = os.path.join(_ROOT_DIR, "datasets", "tello_kinect", "weights", "tello_yolov8n_score.pt")
    ded_wt = score_wt if os.path.isfile(score_wt) else TELLO_DEDICATED_WT
    global CONF_DRONE, TRACK_SCORE_WEAK_MIN
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
        return model
    raise SystemExit(f"missing weights: world={MODEL_NAME}")


_person_model = None


def load_person_model():
    """专用 drone 权重开启时，另载 YOLO-World 只检 person。"""
    global _person_model
    if _person_model is not None:
        return _person_model
    if not os.path.exists(MODEL_NAME):
        return None
    m = YOLO(MODEL_NAME)
    if hasattr(m, "set_classes"):
        m.set_classes(PERSON_ONLY_CLASSES)
    _person_model = m
    print(f"[model] person-only World -> {MODEL_NAME}")
    return _person_model


def read_meta():
    """读取 depth_meta.json（宽高/单位/内参）"""
    with open(os.path.join(KINECT_DIR, "depth_meta.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def _read_bytes(path, retries=2, delay=0.005):
    """读文件；桥接每 50ms 就替换一次文件，撞上替换瞬间直接放弃本帧（下帧马上就来）

    不要长重试：20fps 源头下每帧只有 50ms 窗口，等 180ms 反而拖垮帧率。
    """
    last = None
    for _ in range(retries):
        try:
            return np.fromfile(path, dtype=np.uint8)
        except OSError as e:      # PermissionError 是 OSError 子类：正在被替换
            last = e
            time.sleep(delay)
    raise RuntimeError(f"{os.path.basename(path)} 正被写入，跳过本帧")


def read_color():
    """读本地 color.jpg（用 imdecode 规避中文路径问题）"""
    data = _read_bytes(os.path.join(KINECT_DIR, "color.jpg"))
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def read_depth(meta):
    """读 depth_raw.bin → uint16 深度矩阵（毫米）"""
    data = _read_bytes(os.path.join(KINECT_DIR, "depth_raw.bin"))
    raw = data.view("<u2")
    h, w = int(meta["height"]), int(meta["width"])
    if raw.size != h * w:
        raise ValueError(f"深度尺寸不符：文件 {raw.size} 像素，meta 期望 {h*w}")
    return raw.reshape(h, w)


def box_depth_mm(depth, box):
    """取框中心区域内的有效深度中位数（毫米），无有效值返回 None"""
    h, w = depth.shape
    x1, y1, x2, y2 = box
    # 收缩到中心 BOX_INNER 区域
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    half_w = (x2 - x1) * BOX_INNER / 2.0
    half_h = (y2 - y1) * BOX_INNER / 2.0
    ix1 = max(0, int(cx - half_w)); ix2 = min(w - 1, int(cx + half_w))
    iy1 = max(0, int(cy - half_h)); iy2 = min(h - 1, int(cy + half_h))
    if ix2 <= ix1 or iy2 <= iy1:
        return None
    patch = depth[iy1:iy2 + 1, ix1:ix2 + 1].astype(np.float32)
    valid = patch[(patch >= DEPTH_MIN_M * 1000) & (patch <= DEPTH_MAX_M * 1000)]
    if valid.size < 8:                 # 有效像素太少 → 认为该框没有可用深度
        return None
    return float(np.median(valid))


def to_camera_xyz(u_px, v_px, z_mm, meta):
    """针孔模型反投影：彩色像素 + 深度 → 相机坐标（米）
    X 右为正，Y 下为正，Z 前为正（Kinect 相机坐标系约定）
    """
    fx, fy = float(meta["fx"]), float(meta["fy"])
    cx, cy = float(meta["cx"]), float(meta["cy"])
    z = z_mm / 1000.0
    x = (u_px - cx) * z / fx
    y = (v_px - cy) * z / fy
    return round(x, 3), round(y, 3), round(z, 3)


def physical_size_m(box_color_px, z_mm, meta, sx=1.0, sy=1.0):
    """由 2D 框像素尺寸 + 深度反算物体真实宽高（米）。

    这是把 YOLO-World 的开放词汇误检压下去的关键：光看 2D 框，
    墙上一个插座、遥控器都可能是"drone"；但把它换算成物理尺寸后，
    只有真正落在 Tello 尺寸量级（6~45cm）上的候选才被采信。

    坑（验证过）：meta 里的 fx/fy 是**深度网格**分辨率的内参（当前 960x540 下
    fx=532.159，反算水平 FOV=84.1° 与 Kinect v2 彩色官方值精确吻合），而 YOLO 的框
    在彩色图坐标系。若两者分辨率不同，必须先把框乘 sx/sy 缩到深度网格再算 ——
    当前 bridge 输出彩图与深度同为 960x540，sx=sy=1.0；保留乘法是为了
    bridge 恢复 1920x1080 输出时自动适配。误差主要来源是深度中位数
    取到背景（布艺表面红外穿透），硬质物体（如 Tello）误差小得多。
    """
    fx, fy = float(meta["fx"]), float(meta["fy"])
    x1, y1, x2, y2 = box_color_px
    z = z_mm / 1000.0
    return abs(x2 - x1) * sx * z / fx, abs(y2 - y1) * sy * z / fy


def pick_device(model):
    """优先用 CUDA；若 torchvision 与 torch 版本不匹配导致 CUDA 算子不可用，自动回落 CPU。

    本机 torch 2.12.1+cu126 与已装 torchvision 不匹配，torchvision::nms 没有 CUDA 实现，
    直接指定 device="cuda" 会抛 NotImplementedError，因此这里显式探测一次。
    """
    try:
        model(np.zeros((64, 64, 3), dtype=np.uint8), verbose=False, imgsz=64, device="cuda:0")
        return "cuda:0"
    except Exception as e:
        print(f"[device] CUDA 不可用（{type(e).__name__}），改用 CPU 推理")
        return "cpu"


def _draw_dashed_rect(frame, pt1, pt2, color, thickness=2, dash=14, gap=10):
    """Dashed rectangle for short display-only hold (not a real detection)."""
    x1, y1 = pt1
    x2, y2 = pt2
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1

    def _seg(a, b, horizontal):
        x_a, y_a = a
        x_b, y_b = b
        length = abs((x_b - x_a) if horizontal else (y_b - y_a))
        if length <= 0:
            return
        pos = 0
        draw = True
        while pos < length:
            step = min(dash if draw else gap, length - pos)
            if draw:
                if horizontal:
                    p0 = (x_a + pos if x_b >= x_a else x_a - pos, y_a)
                    p1 = (x_a + pos + step if x_b >= x_a else x_a - pos - step, y_a)
                else:
                    p0 = (x_a, y_a + pos if y_b >= y_a else y_a - pos)
                    p1 = (x_a, y_a + pos + step if y_b >= y_a else y_a - pos - step)
                cv2.line(frame, p0, p1, color, thickness)
            pos += step
            draw = not draw

    _seg((x1, y1), (x2, y1), True)
    _seg((x1, y2), (x2, y2), True)
    _seg((x1, y1), (x1, y2), False)
    _seg((x2, y1), (x2, y2), False)


def annotate(frame, objects):
    """Draw YOLO boxes. held=True -> translucent dashed orange (display only)."""
    for o in objects:
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
        else:
            box_color = MINT if has_depth else GRAY
            thickness = 3

        locked = bool(o.get("track_locked") or _track_score.get("locked"))
        if is_flow and is_drone:
            # 青色虚线：光流+深度续跟（区别 YOLO 实线橙框）
            overlay = frame.copy()
            cv2.rectangle(overlay, (x1, y1), (x2, y2), DEPTHFLOW_COLOR, -1)
            cv2.addWeighted(overlay, 0.14, frame, 0.86, 0, frame)
            _draw_dashed_rect(frame, (x1, y1), (x2, y2), DEPTHFLOW_COLOR, thickness=2)
            box_color = DEPTHFLOW_COLOR
        elif is_held and is_drone:
            overlay = frame.copy()
            cv2.rectangle(overlay, (x1, y1), (x2, y2), box_color, -1)
            cv2.addWeighted(overlay, 0.18, frame, 0.82, 0, frame)
            _draw_dashed_rect(frame, (x1, y1), (x2, y2), box_color, thickness=2)
        elif is_drone and not locked and not (verified and not is_held):
            _draw_dashed_rect(frame, (x1, y1), (x2, y2), box_color, thickness=2)
        else:
            thick = 4 if (is_drone and (locked or verified)) else thickness
            cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, thick)

        text = "DRONE" if is_drone else o["label"]
        if is_flow:
            text += " FLOW"
        if is_held:
            text += " HOLD"
        if o.get("track_score") is not None and is_drone:
            text += f" s{float(o['track_score']):.2f}"
            if o.get("track_locked"):
                text += "L"
        if has_depth:
            text += f" {o['z']:.1f}m"
        if is_drone and not is_held:
            text += " OK" if verified else " ?"
            if o.get("phys"):
                w_cm, h_cm = (v * 100 for v in o["phys"])
                text += f" {w_cm:.0f}x{h_cm:.0f}cm"
        text += f"  {o['conf']:.2f}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.66, 2)
        top = max(0, y1 - th - 10)
        cv2.rectangle(frame, (x1, top), (x1 + tw + 10, top + th + 10), box_color, -1)
        cv2.putText(frame, text, (x1 + 5, top + th + 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.66, (16, 20, 24), 2)
    return frame



STREAM_W = 960        # 推流分辨率宽（等比缩放）——体积降 4 倍，编码/传输/解码全面提速
STREAM_JPEG_Q = 72    # 推流 JPEG 质量

# 同机共享文件推流：写 annotated.jpg，塔台后台线程读。
# 不走 HTTP：Flask 内置服务器不支持 keep-alive，12fps 每帧新建 TCP 连接
# 会堆积 1500+ TIME_WAIT → 耗尽端口 + 撑爆内核非分页池 → 全系统 USB 输入假死。
ANNOTATED_JPG = os.path.join(KINECT_DIR, "annotated.jpg")

# 低频上报（每 2 秒一次检测结果）继续走 HTTP，量小无碍
SESSION = requests.Session()


def push_frame(frame):
    """把画好框的画面缩放到推流分辨率、转 JPEG，原子写入共享文件"""
    h, w = frame.shape[:2]
    if w > STREAM_W:
        nh = round(h * STREAM_W / w / 2) * 2   # 保持偶数尺寸
        frame = cv2.resize(frame, (STREAM_W, nh), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, STREAM_JPEG_Q])
    if not ok:
        return
    raw = buf.tobytes()
    tmp = ANNOTATED_JPG + ".tmp"
    # Windows: 塔台/浏览器读 annotated.jpg 时 os.replace 常报 WinError 5。
    # 策略：replace 重试 → 失败则原地覆盖写 → 再失败才打日志。
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
        for _ in range(5):
            try:
                os.replace(tmp, ANNOTATED_JPG)
                break
            except OSError:
                time.sleep(0.02)
        else:
            with open(ANNOTATED_JPG, "wb") as f:
                f.write(raw)
            try:
                os.remove(tmp)
            except OSError:
                pass
    except OSError as e:
        try:
            with open(ANNOTATED_JPG, "wb") as f:
                f.write(raw)
        except OSError as e2:
            print(f"[frame] 写文件失败: {e2}")




def push_pose(objects):
    """把本帧「真实」的 Tello 位姿 UDP 推给塔台（供控制器闭环）。

    只推 verified 且 **非 held** 的检测：held 是 DRONE_HOLD_S 窗口内回灌的旧坐标，
    拿它做闭环会让飞机照几秒前的位置乱飘 —— 这是漂移的主要来源之一。
    本帧没看到也照发（只带 ts），让塔台能区分「视觉在跑但没目标」和「视觉进程挂了」。
    """
    # 只要本帧真实检测坐标；verified 优先；score 锁定后也允许邻域弱检（仍须 z 有效）
    locked = bool(_track_score.get("locked"))
    cands = []
    for o in objects:
        if not o.get("drone") or o.get("held") or o.get("z") is None:
            continue
        if not (POSE_Z_MIN <= o["z"] <= POSE_Z_MAX):
            continue
        if o.get("verified") or (locked and (o.get("track_locked") or _near_last_box(o))):
            cands.append(o)
    # 原生检出优先：promote / 补扫出来的"疑似 drone"只有在没有原生检出时才启用，
    # 否则会把远处的杯子瓶子当成 Tello 报给控制器（那些 conf 往往还更高，会抢赢）。
    # YOLO / 非续跟优先；depthflow 仅作补洞
    native = [o for o in cands
              if not o.get("promoted_from") and not o.get("second_pass")
              and o.get("det_src") != "depthflow" and o.get("track_mode") != "depthflow"]
    flow = [o for o in cands
            if o.get("det_src") == "depthflow" or o.get("track_mode") == "depthflow"]
    pool = native or flow or cands
    best = None
    if pool:
        best = _select_pose_candidate(pool)
        if best is not None:
            _last_pose.update(x=best["x"], y=best["y"], z=best["z"], ts=time.time())
            box = best.get("box")
            if box is not None:
                now_b = time.time()
                old_b = _last_box.get("box")
                old_ts = float(_last_box.get("ts") or 0)
                cx = 0.5 * (box[0] + box[2])
                cy = 0.5 * (box[1] + box[3])
                vx = float(_last_box.get("vx") or 0.0)
                vy = float(_last_box.get("vy") or 0.0)
                if old_b is not None and old_ts > 0:
                    dt = now_b - old_ts
                    if 0.02 < dt < 1.0:
                        ocx = 0.5 * (old_b[0] + old_b[2])
                        ocy = 0.5 * (old_b[1] + old_b[3])
                        raw_vx = (cx - ocx) / dt
                        raw_vy = (cy - ocy) / dt
                        a = 0.55
                        vx = a * raw_vx + (1.0 - a) * vx
                        vy = a * raw_vy + (1.0 - a) * vy
                _last_box.update(box=tuple(box), ts=now_b, vx=vx, vy=vy)
    else:
        _dbg_no_pose(objects)
    payload = {"ts": time.time(), "src": "kinect-yolo3d"}
    if best is not None:
        payload.update(x=best["x"], y=best["y"], z=best["z"],
                       conf=best.get("conf"), label=best.get("label"))
        # held 已在上面剔除；这里只标记真正进 UDP 的续跟位姿
        if best.get("det_src") == "depthflow" or best.get("track_mode") == "depthflow":
            print(f"[pose] det_src=depthflow x={best['x']:+.3f} y={best['y']:+.3f} "
                  f"z={best['z']:.3f} conf={best.get('conf')} "
                  f"score={_track_score.get('score', 0):.2f}")
    raw = json.dumps(payload).encode("utf-8")
    for addr in POSE_TARGETS:
        try:
            _pose_sock.sendto(raw, addr)
        except OSError:
            pass


DRONE_ONLY_CLASSES = [
    # 无 person：单独二扫，避免人框抢走 CLIP 注意力
    "toy drone", "drone", "tello", "handheld drone", "white quadcopter", "flying drone",
]


def _drone_second_pass(model, meta, device, color, depth, sx, sy, objects):
    """主扫（含 person）漏掉 drone 时：无 person 词表再扫一遍。不做 promote。"""
    ch, cw = color.shape[:2]
    frame_area = float(cw * ch)
    try:
        model.set_classes(DRONE_ONLY_CLASSES)
        with torch.no_grad():
            results = model(color, verbose=False, imgsz=640, device=device, conf=CONF_DRONE)
    except Exception as e:
        print(f"[drone-pass] fail: {e}")
        return objects
    finally:
        try:
            model.set_classes(WORLD_CLASSES)
        except Exception:
            pass
    extra = []
    for r in results:
        names = r.names if getattr(r, "names", None) else {}
        for b in r.boxes:
            if isinstance(names, dict):
                raw_label = names.get(int(b.cls[0]), "?")
            else:
                raw_label = str(names[int(b.cls[0])])
            raw_l = str(raw_label).lower()
            label = DRONE_ALIAS.get(raw_label, DRONE_ALIAS.get(raw_l, raw_label))
            conf = round(float(b.conf[0]), 3)
            if label != "drone":
                if not any(k in raw_l for k in ("drone", "tello", "quad", "uav")):
                    continue
                label = "drone"
            if conf < CONF_DRONE:
                continue
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
            area = (x2 - x1) * (y2 - y1)
            if area > 0.22 * frame_area:
                continue
            if area < DRONE_MIN_AREA_PX:
                continue
            u_c, v_c = (x1 + x2) / 2.0 * sx, (y1 + y2) / 2.0 * sy
            z_mm = box_depth_mm(depth, (x1 * sx, y1 * sy, x2 * sx, y2 * sy))
            x = y = z = None
            phys = None
            verified = None
            if z_mm is not None:
                x, y, z = to_camera_xyz(u_c, v_c, z_mm, meta)
                phys = physical_size_m((x1, y1, x2, y2), z_mm, meta, sx, sy)
                if phys:
                    verified = DRONE_MIN_M <= max(phys) <= DRONE_MAX_M
            extra.append({"label": "drone", "conf": conf, "x": x, "y": y, "z": z,
                          "box": (x1, y1, x2, y2), "drone": True,
                          "verified": verified,
                          "phys": tuple(round(v, 3) for v in phys) if phys else None,
                          "second_pass": True, "det_src": "drone_pass"})
            print(f"[drone-pass] conf={conf} verified={verified} phys={phys} z={z}")
    if extra:
        # verified 优先；同档取更小框（更像手持 Tello）
        def _score(o):
            box = o.get("box") or (0, 0, 0, 0)
            area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
            return (1 if o.get("verified") else 0, o.get("conf") or 0, -area)
        extra.sort(key=_score, reverse=True)
        objects = list(objects) + [extra[0]]
    return objects




# 短时保持状态（进程内）：抗 YOLO 偶发漏检导致的 verified 闪断
_drone_hold = {"obj": None, "until": 0.0}


def _box_center(o):
    box = o.get("box")
    if not box or len(box) < 4:
        return None
    return ((box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5)


def apply_drone_hold(objects, now=None):
    """有 verified drone 则刷新 hold（单目标跟踪）；丢失则在窗口内回灌并继续画框。

    同一帧多个 bottle→drone 时，优先续跟上一帧中心最近的目标，减少橙框乱跳。
    """
    now = time.time() if now is None else now
    others = [o for o in objects if not o.get("drone")]
    verified = [o for o in objects
                if o.get("drone") and o.get("verified") and o.get("z") is not None]

    def _pick(cands):
        prev = _drone_hold.get("obj")
        pc = _box_center(prev) if prev else None
        if not cands:
            return None
        if pc is None:
            return max(cands, key=lambda o: (o.get("conf") or 0))
        def score(o):
            c = _box_center(o)
            if not c:
                return (1e9, 0)
            d = ((c[0] - pc[0]) ** 2 + (c[1] - pc[1]) ** 2) ** 0.5
            return (d, -(o.get("conf") or 0))
        return min(cands, key=score)

    if verified:
        best = _pick(verified)
        held = dict(best)
        held["held"] = False
        _drone_hold["obj"] = held
        # 锁定时 hold 稍长，分数低则更快清掉幽灵框
        hold_s = DRONE_HOLD_S * (1.6 if _track_score.get("locked") else 1.0)
        if _track_score.get("score", 0) <= TRACK_SCORE_DROP:
            hold_s = 0.0
        _drone_hold["until"] = now + hold_s
        held["track_score"] = round(float(_track_score.get("score") or 0), 3)
        held["track_locked"] = bool(_track_score.get("locked"))
        return others + [held]

    if (_drone_hold.get("obj") and now <= _drone_hold["until"]
            and float(_track_score.get("score") or 0) > TRACK_SCORE_DROP):
        ghost = dict(_drone_hold["obj"])
        ghost["held"] = True
        ghost["track_score"] = round(float(_track_score.get("score") or 0), 3)
        ghost["track_locked"] = bool(_track_score.get("locked"))
        return others + [ghost]
    return objects


    if _drone_hold.get("obj") and now <= _drone_hold["until"]:
        ghost = dict(_drone_hold["obj"])
        ghost["held"] = True
        # 非 drone 物体保留；去掉本帧未 verified 的 drone 噪声，换上 hold
        others = [o for o in objects if not o.get("drone")]
        return others + [ghost]
    return objects



def promote_mislabeled_drone(objects):
    """YOLO-World 常把白色小 Tello 标成 bottle/cell phone。
    若物理尺寸落在 Tello 窗内且长宽比不太夸张，提升为 drone 并走 verified。
    """
    out = []
    for o in objects:
        if o.get("drone"):
            out.append(o)
            continue
        label = o.get("label")
        phys = o.get("phys")
        if label not in PROMOTE_LABELS or not phys or o.get("z") is None:
            out.append(o)
            continue
        mx, mn = max(phys), min(phys)
        if not (DRONE_MIN_M <= mx <= DRONE_MAX_M):
            out.append(o)
            continue
        if mn <= 1e-6 or (mx / mn) > PROMOTE_MAX_ASPECT:
            out.append(o)
            continue
        o2 = dict(o)
        o2["label"] = "drone"
        o2["drone"] = True
        o2["verified"] = True
        o2["promoted_from"] = label
        out.append(o2)
        print(f"[promote] {label} conf={o.get('conf')} phys={phys} z={o.get('z')} -> drone verified")
    return out



def _track_center_px(age_s=0.0):
    """Last box center, optionally velocity-extrapolated (ROI / neighbor only)."""
    box = _last_box.get("box")
    if box is None:
        return None
    cx = 0.5 * (box[0] + box[2])
    cy = 0.5 * (box[1] + box[3])
    if age_s > 0:
        cx += float(_last_box.get("vx") or 0.0) * age_s
        cy += float(_last_box.get("vy") or 0.0) * age_s
    return cx, cy


def _near_last_box(o, max_px=None):
    """True if candidate box is near last/extrapolated track (handheld neighbor)."""
    max_px = NEIGHBOR_PX if max_px is None else max_px
    box = o.get("box")
    if not box:
        return False
    age = time.time() - float(_last_box.get("ts") or 0)
    tc = _track_center_px(age_s=min(age, ROI_MAX_AGE))
    if tc is None:
        return False
    cx = 0.5 * (box[0] + box[2])
    cy = 0.5 * (box[1] + box[3])
    return ((cx - tc[0]) ** 2 + (cy - tc[1]) ** 2) ** 0.5 <= max_px


def _select_pose_candidate(pool):
    """Pick pose for push_pose. Far new targets need multi-frame or higher conf.
    Neighbor-of-last-box accepted immediately (fast handheld). Never uses hold /
    extrapolated coords as the pose itself.
    """
    global _pending_new
    neighbors = [o for o in pool if _near_last_box(o)]
    if neighbors:
        _pending_new = {"key": None, "hits": 0}
        return max(neighbors, key=lambda o: o.get("conf") or 0)

    lp = _last_pose
    if lp["x"] is not None and (time.time() - lp["ts"]) < 8.0:
        ranked = sorted(pool, key=lambda o: ((o["x"] - lp["x"]) ** 2
                                             + (o["y"] - lp["y"]) ** 2
                                             + (o["z"] - lp["z"]) ** 2))
        nearest = ranked[0]
        dist = ((nearest["x"] - lp["x"]) ** 2
                + (nearest["y"] - lp["y"]) ** 2
                + (nearest["z"] - lp["z"]) ** 2) ** 0.5
        if dist <= NEW_TARGET_DIST_M:
            _pending_new = {"key": None, "hits": 0}
            return nearest
        key = (round(nearest["x"], 2), round(nearest["y"], 2), round(nearest["z"], 2))
        conf = nearest.get("conf") or 0
        if conf >= NEW_TARGET_CONF:
            _pending_new = {"key": None, "hits": 0}
            return nearest
        if _pending_new.get("key") == key:
            _pending_new["hits"] = int(_pending_new.get("hits") or 0) + 1
        else:
            _pending_new = {"key": key, "hits": 1}
        if _pending_new["hits"] >= NEW_TARGET_NEED:
            _pending_new = {"key": None, "hits": 0}
            return nearest
        for o in ranked[1:]:
            d2 = ((o["x"] - lp["x"]) ** 2 + (o["y"] - lp["y"]) ** 2
                  + (o["z"] - lp["z"]) ** 2) ** 0.5
            if d2 <= NEW_TARGET_DIST_M:
                return o
        return None
    _pending_new = {"key": None, "hits": 0}
    return max(pool, key=lambda o: o.get("conf") or 0)




def _sample_box_points(box, n=6):
    """Uniform grid points inside box (color pixel coords) for LK."""
    x1, y1, x2, y2 = [float(v) for v in box]
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    xs = np.linspace(x1 + 0.15 * (x2 - x1), x2 - 0.15 * (x2 - x1), n)
    ys = np.linspace(y1 + 0.15 * (y2 - y1), y2 - 0.15 * (y2 - y1), n)
    pts = np.array([(x, y) for y in ys for x in xs], dtype=np.float32).reshape(-1, 1, 2)
    return pts


def _seed_flow_track(drone_obj, color_bgr):
    """Seed / refresh optical-flow track from a YOLO (or prior) drone hit."""
    global _flow_state
    box = drone_obj.get("box")
    z = drone_obj.get("z")
    if not box or z is None:
        return
    gray = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2GRAY)
    now = time.time()
    _flow_state.update(
        prev_gray=gray,
        box=tuple(float(v) for v in box),
        z=float(z),
        phys=drone_obj.get("phys"),
        yolo_ts=now,
        flow_start=0.0,
        active=True,
    )


def _clear_flow_track(reason=""):
    global _flow_state
    if _flow_state.get("active") and reason:
        print(f"[depthflow] clear ({reason})")
    _flow_state.update(
        prev_gray=None, box=None, z=None, phys=None,
        yolo_ts=0.0, flow_start=0.0, active=False,
        last_fail=False, lock_hold_until=0.0,
    )


def _depthflow_jump_reason(x, y, z):
    """续跟 xyz 相对光流 z / _last_pose 跳太多则拒绝（背景粘连）。

    返回原因串；没有超阈返回 None。没有新鲜参考时放行，交给尺寸和 z 窗。
    """
    reasons = []
    z_lim = DEPTHFLOW_Z_JUMP_M
    xyz_lim = DEPTHFLOW_JUMP_M
    fz = _flow_state.get("z")
    if fz is not None and abs(z - float(fz)) > z_lim:
        reasons.append(f"dz_flow={z - float(fz):+.2f}>{z_lim:.2f}")
    lp = _last_pose
    if lp.get("x") is not None and lp.get("z") is not None:
        age = time.time() - float(lp.get("ts") or 0.0)
        if 0.0 <= age <= max(2.0, DEPTHFLOW_MAX_S + 0.5):
            dx = float(x) - float(lp["x"])
            dy = float(y) - float(lp.get("y") or 0.0)
            dzp = float(z) - float(lp["z"])
            if abs(dzp) > z_lim:
                reasons.append(f"dz_pose={dzp:+.2f}>{z_lim:.2f}")
            dist = (dx * dx + dy * dy + dzp * dzp) ** 0.5
            if dist > xyz_lim:
                reasons.append(f"dxyz={dist:.2f}>{xyz_lim:.2f}")
    return " ".join(reasons) if reasons else None


def _depth_blob_at(depth, meta, sx, sy, cx_color, cy_color, bw, bh, z_ref,
                   search_scale=1.8):
    """Find depth points near predicted color center within z_ref±DZ.
    Returns (box_color, z_mm, xyz, phys, n_pts) or None.
    Pose comes from current depth median/centroid — never pure extrapolation.
    跳变门不在这里收紧搜索带：找到 xyz 后由 try_depthflow 对 _last_pose / 光流 z 拒绝。
    """
    h, w = depth.shape
    # search window in color px, then map to depth
    half_w = max(12.0, 0.5 * bw * search_scale)
    half_h = max(12.0, 0.5 * bh * search_scale)
    x1c = cx_color - half_w
    y1c = cy_color - half_h
    x2c = cx_color + half_w
    y2c = cy_color + half_h
    ix1 = max(0, int(x1c * sx)); ix2 = min(w - 1, int(x2c * sx))
    iy1 = max(0, int(y1c * sy)); iy2 = min(h - 1, int(y2c * sy))
    if ix2 <= ix1 or iy2 <= iy1:
        return None
    patch = depth[iy1:iy2 + 1, ix1:ix2 + 1].astype(np.float32)
    z_lo = max(DEPTH_MIN_M * 1000, (z_ref - DEPTHFLOW_DZ_M) * 1000)
    z_hi = min(DEPTH_MAX_M * 1000, (z_ref + DEPTHFLOW_DZ_M) * 1000)
    mask = (patch >= z_lo) & (patch <= z_hi)
    n_pts = int(mask.sum())
    if n_pts < DEPTHFLOW_MIN_PTS:
        return None
    ys_idx, xs_idx = np.where(mask)
    z_vals = patch[ys_idx, xs_idx]
    z_mm = float(np.median(z_vals))
    # centroid in depth pixels
    u_d = float(xs_idx.mean()) + ix1
    v_d = float(ys_idx.mean()) + iy1
    # map back to color center; keep previous box size
    cx = u_d / sx if sx else u_d
    cy = v_d / sy if sy else v_d
    box = (cx - bw * 0.5, cy - bh * 0.5, cx + bw * 0.5, cy + bh * 0.5)
    xyz = to_camera_xyz(u_d, v_d, z_mm, meta)
    phys = physical_size_m(box, z_mm, meta, sx, sy)
    return box, z_mm, xyz, phys, n_pts


def try_depthflow(meta, color=None, depth=None):
    """If YOLO missed but we have a recent track: LK flow + depth blob.
    Returns a drone object (det_src=depthflow) or None.
    """
    global _flow_state
    st = _flow_state
    if not st.get("active") or st.get("prev_gray") is None or st.get("box") is None:
        return None
    now = time.time()
    # must have had a real YOLO seed recently enough to start; once flowing, cap total
    yolo_age = now - float(st.get("yolo_ts") or 0)
    if yolo_age > DEPTHFLOW_MAX_S + 0.35:
        # allow brief grace after last YOLO before hard clear via caller
        pass
    if color is None:
        try:
            color = read_color()
        except Exception:
            return None
    if color is None:
        return None
    if depth is None:
        try:
            depth = read_depth(meta)
        except Exception:
            return None

    ch, cw = color.shape[:2]
    sx = float(meta["width"]) / cw
    sy = float(meta["height"]) / ch
    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    prev = st["prev_gray"]
    if prev.shape != gray.shape:
        _clear_flow_track("resolution-change")
        return None

    box = st["box"]
    x1, y1, x2, y2 = box
    bw, bh = max(8.0, x2 - x1), max(8.0, y2 - y1)
    pts = _sample_box_points(box, n=5)
    if pts is None:
        return None

    pts2, status, _err = cv2.calcOpticalFlowPyrLK(
        prev, gray, pts, None,
        winSize=(21, 21), maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
    )
    if pts2 is None or status is None:
        return None
    good = status.reshape(-1) == 1
    if int(good.sum()) < 6:
        return None
    disp = (pts2.reshape(-1, 2) - pts.reshape(-1, 2))[good]
    dx = float(np.median(disp[:, 0]))
    dy = float(np.median(disp[:, 1]))
    # reject absurd jumps (half-frame)
    if abs(dx) > cw * 0.35 or abs(dy) > ch * 0.35:
        return None

    cx = 0.5 * (x1 + x2) + dx
    cy = 0.5 * (y1 + y2) + dy
    # clamp center inside frame
    cx = float(np.clip(cx, bw * 0.5, cw - bw * 0.5))
    cy = float(np.clip(cy, bh * 0.5, ch - bh * 0.5))

    z_ref = float(st.get("z") or 1.0)
    blob = _depth_blob_at(depth, meta, sx, sy, cx, cy, bw, bh, z_ref)
    if blob is None:
        # 团块失败：不把光流位移写进 box / 不改 z。锚留在上一帧真实深度。
        print(f"[depthflow] dx={dx:.1f} dy={dy:.1f} z=None (no blob) "
              f"score={_track_score.get('score', 0):.2f} box-hold")
        return None

    new_box, z_mm, xyz, phys, n_pts = blob
    x, y, z = xyz
    jump = _depthflow_jump_reason(x, y, z)
    if jump:
        # 背景粘连：不提交框，也不把这帧 z 写进 flow state。调用方走失败衰减。
        print(f"[depthflow] dx={dx:.1f} dy={dy:.1f} z={z:.2f} REJECT jump {jump} "
              f"score={_track_score.get('score', 0):.2f}")
        return None
    verified = False
    if phys:
        verified = DRONE_MIN_M <= max(phys) <= DRONE_MAX_M
    if not verified:
        print(f"[depthflow] dx={dx:.1f} dy={dy:.1f} z={z:.2f} phys={phys} REJECT size "
              f"score={_track_score.get('score', 0):.2f}")
        return None
    if not (POSE_Z_MIN <= z <= POSE_Z_MAX):
        print(f"[depthflow] dx={dx:.1f} dy={dy:.1f} z={z:.2f} REJECT z-range "
              f"score={_track_score.get('score', 0):.2f}")
        return None

    if not st.get("flow_start"):
        st["flow_start"] = now
    flow_age = now - float(st["flow_start"])
    # also bound by time since last YOLO
    if flow_age > DEPTHFLOW_MAX_S or yolo_age > DEPTHFLOW_MAX_S + 0.15:
        _clear_flow_track("timeout")
        return None

    st["prev_gray"] = gray
    st["box"] = tuple(new_box)
    st["z"] = z
    st["phys"] = tuple(round(v, 3) for v in phys) if phys else None

    print(f"[depthflow] det_src=depthflow dx={dx:.1f} dy={dy:.1f} z={z:.2f} n={n_pts} "
          f"age={flow_age:.2f}s score={_track_score.get('score', 0):.2f}")

    return {
        "label": "drone",
        "conf": 0.03,
        "x": x, "y": y, "z": z,
        "box": tuple(new_box),
        "drone": True,
        "verified": True,          # real depth + size gate → may push_pose
        "phys": tuple(round(v, 3) for v in phys) if phys else None,
        "det_src": "depthflow",
        "track_mode": "depthflow",
        "held": False,
        "second_pass": False,
        "promoted_from": None,
    }


def maybe_attach_depthflow(objects, meta):
    """YOLO 优先；无可用 drone 时尝试 depthflow 续跟。"""
    now = time.time()
    yolo_drones = [
        o for o in objects
        if o.get("drone") and not o.get("held")
        and o.get("det_src") != "depthflow"
        and o.get("track_mode") != "depthflow"
    ]
    yolo_good = [
        o for o in yolo_drones
        if o.get("verified") and o.get("z") is not None
    ]
    try:
        color = read_color()
    except Exception:
        color = None

    if yolo_good and color is not None:
        strong = [o for o in yolo_good if (o.get("conf") or 0) >= CONF_DRONE]
        if strong:
            best = max(strong, key=lambda o: o.get("conf") or 0)
            _seed_flow_track(best, color)
        return objects

    # weak YOLO near track: still seed, don't need depthflow this frame
    if yolo_drones and color is not None:
        near = [o for o in yolo_drones if _near_last_box(o) or o.get("z") is not None]
        if near:
            best = max(near, key=lambda o: o.get("conf") or 0)
            if best.get("z") is not None:
                _seed_flow_track(best, color)
                return objects

    # no YOLO drone → depthflow 仅在已 LOCK 后启用（禁止从未锁定用光流刷分）
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
        return objects

    flow_obj = try_depthflow(meta, color=color)
    if flow_obj is None:
        # accelerate decay hint via flag for update_track_score
        _flow_state["last_fail"] = True
        return objects
    _flow_state["last_fail"] = False
    # keep persons + depthflow drone
    persons = [o for o in objects if o.get("label") == "person"]
    return persons + [flow_obj]


def update_track_score(objects):
    """根据本帧 drone 检测更新轨迹分数。返回 (score, locked, best_drone_or_None)。"""
    global _track_score, _track_score_log_ts
    now = time.time()
    drones = [o for o in objects if o.get("drone") and not o.get("held")]
    yolo_verified = [o for o in drones
                     if o.get("verified") and o.get("z") is not None
                     and o.get("det_src") != "depthflow" and o.get("track_mode") != "depthflow"]
    depthflow_hits = [o for o in drones
                      if o.get("det_src") == "depthflow" or o.get("track_mode") == "depthflow"]
    near_weak = []
    for o in drones:
        if o in yolo_verified or o in depthflow_hits:
            continue
        conf = o.get("conf") or 0
        if conf < TRACK_SCORE_WEAK_MIN:
            continue
        if _near_last_box(o) or _track_score["score"] >= TRACK_SCORE_LOCK:
            near_weak.append(o)

    # 仅 conf>=CONF_DRONE 的 verified YOLO 可加分；弱检不加分；depthflow 仅已锁定时可维持
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
        _flow_state["last_fail"] = False
        _flow_state["lock_hold_until"] = 0.0
    elif hit_kind == "depthflow" and best is not None:
        # 已锁定才进得来。成功帧只加分、不衰减。
        # 刚越过 LOCK（0.42）时 +0.10 挡不住下一帧 DECAY(0.14) / FAIL_DECAY(0.20)，
        # maybe_attach_depthflow 会立刻停。抬到至少 LOCK+一帧最大衰减。
        one_miss = max(TRACK_SCORE_DECAY, DEPTHFLOW_DECAY, DEPTHFLOW_FAIL_DECAY)
        sc = min(TRACK_SCORE_MAX, max(sc + DEPTHFLOW_SCORE_GAIN, TRACK_SCORE_LOCK + one_miss))
        _track_score["hits"] = int(_track_score.get("hits") or 0) + 1
        _track_score["misses"] = 0
        yolo_ts = float(_flow_state.get("yolo_ts") or now)
        _flow_state["lock_hold_until"] = yolo_ts + DEPTHFLOW_MAX_S
        _flow_state["last_fail"] = False
    else:
        decay = TRACK_SCORE_DECAY
        if _flow_state.get("last_fail"):
            decay = DEPTHFLOW_FAIL_DECAY
        elif locked_now and _flow_state.get("active"):
            decay = DEPTHFLOW_DECAY
        sc = max(0.0, sc - decay)
        _track_score["misses"] = int(_track_score.get("misses") or 0) + 1
        hold_until = float(_flow_state.get("lock_hold_until") or 0.0)
        if (not _flow_state.get("last_fail") and _flow_state.get("active")
                and hold_until > now and locked_now and sc < TRACK_SCORE_LOCK):
            # 窗口内普通 DECAY 不得单独打掉 LOCK；失败衰减不在此列
            sc = TRACK_SCORE_LOCK
        elif _flow_state.get("last_fail") and sc < TRACK_SCORE_LOCK:
            _flow_state["lock_hold_until"] = 0.0

    if sc <= TRACK_SCORE_DROP:
        sc = 0.0
        _track_score["hits"] = 0

    locked = sc >= TRACK_SCORE_LOCK
    _track_score.update(score=sc, ts=now, locked=locked)

    if now - _track_score_log_ts >= 2.0:
        _track_score_log_ts = now
        tag = "LOCK" if locked else ("track" if sc > TRACK_SCORE_DROP else "idle")
        print(f"[score] {tag} score={sc:.2f} hits={_track_score['hits']} "
              f"miss={_track_score['misses']} best="
              f"{(best.get('conf') if best else None)} det_src={hit_kind or '-'}")

    # annotate score onto best for status/HTTP
    if best is not None:
        best = dict(best)
        best["track_score"] = round(sc, 3)
        best["track_locked"] = locked
    return sc, locked, best


def filter_drones_by_score(objects, score, locked):
    """弱检仅当已锁定或邻域内才保留；verified 始终保留。"""
    out = []
    for o in objects:
        if not o.get("drone") or o.get("held"):
            out.append(o)
            continue
        # depthflow：仅锁定后保留
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
    return out


def _roi_rect(box, w, h, extrapolate=True):
    """ROI crop window. If extrapolate, shift center by last velocity*age (crop only)."""
    x1, y1, x2, y2 = box
    cx = 0.5 * (x1 + x2)
    cy = 0.5 * (y1 + y2)
    if extrapolate:
        age = time.time() - float(_last_box.get("ts") or 0)
        age = max(0.0, min(age, ROI_MAX_AGE))
        cx += float(_last_box.get("vx") or 0.0) * age
        cy += float(_last_box.get("vy") or 0.0) * age
    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)
    side = max(ROI_MIN_PX, ROI_SCALE * max(bw, bh))
    half = side * 0.5
    x0 = int(max(0, cx - half))
    y0 = int(max(0, cy - half))
    x1i = int(min(w, cx + half))
    y1i = int(min(h, cy + half))
    if x1i - x0 < ROI_MIN_PX // 2 or y1i - y0 < ROI_MIN_PX // 2:
        return None
    return x0, y0, x1i, y1i


def _boxes_from_result(results, model_names, ox, oy, sx, sy, meta, depth,
                       drone_mode, source_tag):
    objects = []
    for r in results:
        names = r.names if getattr(r, "names", None) else model_names
        for b in r.boxes:
            if isinstance(names, dict):
                raw_label = names.get(int(b.cls[0]), "?")
            else:
                raw_label = str(names[int(b.cls[0])])
            raw_l = raw_label.lower() if isinstance(raw_label, str) else str(raw_label)
            label = PERSON_ALIAS.get(raw_label, PERSON_ALIAS.get(raw_l,
                    DRONE_ALIAS.get(raw_label, DRONE_ALIAS.get(raw_l, raw_label))))
            conf = round(float(b.conf[0]), 3)
            is_drone = label == "drone"
            is_person = label == "person"
            # 只要人 + 无人机：其它类一律丢掉（不画框、不进 objects）
            if not is_drone and not is_person:
                continue
            # 验收线：drone 必须 conf>=CONF_DRONE（dedicated≈0.15 / world≈0.05）
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
                    continue
            u_c, v_c = (x1 + x2) / 2.0 * sx, (y1 + y2) / 2.0 * sy
            z_mm = box_depth_mm(depth, (x1 * sx, y1 * sy, x2 * sx, y2 * sy))
            x = y = z = None
            phys = None
            verified = None
            if z_mm is not None:
                x, y, z = to_camera_xyz(u_c, v_c, z_mm, meta)
                phys = physical_size_m((x1, y1, x2, y2), z_mm, meta, sx, sy)
                if is_drone and phys:
                    verified = DRONE_MIN_M <= max(phys) <= DRONE_MAX_M
            objects.append({"label": label, "conf": conf, "x": x, "y": y, "z": z,
                            "box": (x1, y1, x2, y2), "drone": is_drone,
                            "verified": verified,
                            "phys": tuple(round(v, 3) for v in phys) if phys else None,
                            "det_src": source_tag})
    return objects


def _merge_roi_full(roi_objs, full_objs):
    """Merge full-frame into ROI results. Persons always kept; drone dedupe only."""
    out = list(roi_objs)
    roi_drones = [o for o in roi_objs if o.get("drone")]
    for fo in full_objs:
        if not fo.get("drone"):
            # person / other kept labels — always merge
            out.append(fo)
            continue
        if not fo.get("verified") and any(r.get("verified") for r in roi_drones):
            continue
        fbox = fo.get("box")
        if not fbox:
            out.append(fo)
            continue
        fcx = 0.5 * (fbox[0] + fbox[2])
        fcy = 0.5 * (fbox[1] + fbox[3])
        dup = False
        for ro in roi_drones:
            rbox = ro.get("box")
            if not rbox:
                continue
            rcx = 0.5 * (rbox[0] + rbox[2])
            rcy = 0.5 * (rbox[1] + rbox[3])
            if ((fcx - rcx) ** 2 + (fcy - rcy) ** 2) ** 0.5 < 40:
                dup = True
                break
        if not dup:
            out.append(fo)
    return out



def detect(model, meta, device, drone_mode=False, person_model=None):
    """ROI (velocity-extrapolated) then full-frame. Always low feed_conf so weak
    drone scores survive; person filtered later by CONF_GENERAL.
    No last_box → always full-frame. Miss drone → drone-only second pass.
    No promote. No hold into returned objects.
    """
    global _infer_i
    color = read_color()
    if color is None:
        raise RuntimeError("color.jpg read failed")
    depth = read_depth(meta)

    ch, cw = color.shape[:2]
    sx = float(meta["width"]) / cw
    sy = float(meta["height"]) / ch

    # 手持/无人机未连塔台时也要检 drone；旧逻辑 drone_mode=False 用 0.25 会把 0.01 档全滤掉
    feed_conf = CONF_DRONE
    _infer_i += 1
    no_track = _last_box.get("box") is None
    do_full = no_track or (_infer_i % FULL_EVERY_N == 0)

    objects = []
    lb = _last_box.get("box")
    lb_age = time.time() - float(_last_box.get("ts") or 0)
    if (not no_track) and lb is not None and lb_age <= ROI_MAX_AGE:
        rect = _roi_rect(lb, cw, ch)
        if rect is not None:
            x0, y0, x1, y1 = rect
            crop = color[y0:y1, x0:x1]
            if crop.size > 0:
                with torch.no_grad():
                    results = model(crop, verbose=False, imgsz=ROI_IMGSZ, device=device, conf=feed_conf)
                names = getattr(model, "names", {})
                objects = _boxes_from_result(
                    results, names, x0, y0, sx, sy, meta, depth, drone_mode, "roi")

    need_full = do_full or not any(
        o.get("drone") and o.get("verified") and o.get("z") is not None for o in objects)
    if need_full:
        with torch.no_grad():
            results = model(color, verbose=False, imgsz=640, device=device, conf=feed_conf)
        names = getattr(model, "names", {})
        full_objs = _boxes_from_result(
            results, names, 0, 0, sx, sy, meta, depth, drone_mode, "full")
        if objects:
            objects = _merge_roi_full(objects, full_objs)
        else:
            objects = full_objs

    if USE_DEDICATED_TELLO:
        # model 是专用 drone 检测器；person 另走 World person-only
        if person_model is not None:
            try:
                with torch.no_grad():
                    pr = person_model(color, verbose=False, imgsz=640, device=device, conf=0.25)
                persons = _boxes_from_result(
                    pr, getattr(person_model, "names", {}), 0, 0, sx, sy, meta, depth,
                    drone_mode, "person")
                persons = [o for o in persons if o.get("label") == "person"]
                objects = persons + [o for o in objects if o.get("drone")]
            except Exception as e:
                print(f"[person] {e}")
        return objects

    # person+drone 同表时 drone 常被稀释 → 无 drone 时单独二扫
    if not any(o.get("drone") for o in objects):
        objects = _drone_second_pass(model, meta, device, color, depth, sx, sy, objects)
    return objects




def draw(frame, objects):
    """把最近一次检测结果画到帧上（原地修改），返回画好的帧"""
    annotate(frame, objects)
    return frame


def describe(objects):
    """打印成一行行可读结果"""
    if not objects:
        return "本帧未检测到目标"
    lines = []
    for o in objects:
        if o.get("z") is None:
            pos = "（无有效深度）"
        else:
            pos = f"前方 {o['z']:.2f}m · 右 {o['x']:+.2f}m · 下 {o['y']:+.2f}m"
        extra = ""
        if o.get("drone"):
            phys = o.get("phys")
            size = f"{(phys[0] * 100):.0f}x{(phys[1] * 100):.0f}cm " if phys else ""
            extra = f"  [无人机 {'已确认' if o.get('verified') else '疑似'} {size}]"
        lines.append(f"  {o['label']:<12} {o['conf']:.2f}   {pos}{extra}")
    return "\n".join(lines)


def read_motion():
    """读桥接程序写的变化度（小文件，每帧都更新）"""
    try:
        with open(os.path.join(KINECT_DIR, "motion.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def frame_mtime():
    """大帧（color.jpg）的修改时间——用来判断是否出现了“新帧”"""
    try:
        return os.path.getmtime(os.path.join(KINECT_DIR, "color.jpg"))
    except OSError:
        return 0.0


def change_level(ratio, changed):
    """把变化度翻译成人能看的档位"""
    if changed:
        return "明显"
    if (ratio or 0) >= 0.005:
        return "轻微"
    return "静止"


def uav_online_from_tower():
    """问塔台：Tello 在网络侧在线吗？

    这是"网络辅助视觉"：塔台用 UDP 握手知道无人机在不在空中，视觉侧据此
    放宽/收紧无人机门限 —— 无人机明明在线时就别放过弱信号，不在线时
    也不该凭空 hallucinate 出一架。返回 None = 塔台没回话，保持现状别乱切。
    """
    try:
        with urllib.request.urlopen(f"{TOWER}/api/status", timeout=2) as r:
            st = json.loads(r.read().decode("utf-8"))
        return bool((st.get("uav") or {}).get("online"))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true",
                    help="只处理一帧并打印，不上报（离线验证）")
    ap.add_argument("--interval", type=float, default=REPORT_INTERVAL,
                    help=f"上报间隔秒（默认 {REPORT_INTERVAL}）")
    args = ap.parse_args()

    print("加载 YOLOv8n ...")
    model = load_model()
    device = pick_device(model)
    print(f"模型就绪（推理设备 {device}）。Kinect 目录: {KINECT_DIR}")
    print(f"[depthflow] MAX_S={DEPTHFLOW_MAX_S:.2f} DZ={DEPTHFLOW_DZ_M:.2f} "
          f"Z_JUMP={DEPTHFLOW_Z_JUMP_M:.2f} JUMP={DEPTHFLOW_JUMP_M:.2f}")

    if args.probe:
        meta = read_meta()
        print(f"深度网格 {meta['width']}×{meta['height']} 单位 {meta['unit']} "
              f"fx={meta['fx']} fy={meta['fy']} cx={meta['cx']} cy={meta['cy']}")
        objects = detect(model, meta, device)
        print("检测结果（相机坐标，米）：")
        print(describe(objects))
        out = os.path.join(os.environ.get("TEMP", "."), "k3d_probe_annotated.jpg")
        cv2.imwrite(out, draw(read_color(), objects))
        print(f"已画框的画面：{out}")
        return

    # ---- 推流与推理解耦：新帧立即画“上一次的框”推流（快），
    #      YOLO 在后台线程算，算完更新框（下一帧用上）。
    #      推理限频 5fps：ultralytics 前后处理占 GIL，连轴转会拖慢推流线程。
    print(f"上报 {REPORT} · 视频流共享文件 {ANNOTATED_JPG}")
    shared = {"objects": [], "busy": False, "meta": read_meta(), "last_infer": 0.0,
              "drone_mode": False, "person_model": None,
              "track_score": 0.0, "track_locked": False}
    if USE_DEDICATED_TELLO:
        shared["person_model"] = load_person_model()
    INFER_INTERVAL = 0.06
    last_report = 0.0
    last_frame_mtime = 0.0
    last_gc = time.time()
    last_uav_check = 0.0
    reports = 0
    fps_count = 0
    fps_t0 = time.time()

    def inference_worker():
        """常驻推理线程（关键修复）。

        旧实现每帧推理都 threading.Thread(...) 新开线程：PyTorch/oneDNN 的
        原生工作区是线程本地(TLS)的，线程销毁后内存不归还 OS，
        每分钟 ~300 个新线程 → ~300MB/min 提交内存泄漏 → 拖垮 USB 控制器。
        单一常驻线程的 TLS 工作区只分配一次，不再增长。
        """
        while True:
            infer_q.get()          # 阻塞等推理任务
            try:
                objs = detect(model, shared["meta"], device,
                              drone_mode=shared["drone_mode"],
                              person_model=shared.get("person_model"))
                objs = maybe_attach_depthflow(objs, shared["meta"])
                score, locked, _best = update_track_score(objs)
                objs = filter_drones_by_score(objs, score, locked)
                # push_pose：YOLO 或 depthflow 的真实深度位姿；held 幽灵不推
                push_pose(objs)
                # depthflow 已是真深度，勿被 HTTP hold 幽灵覆盖
                if any(o.get("det_src") == "depthflow" for o in objs):
                    shared["objects"] = objs
                else:
                    shared["objects"] = apply_drone_hold(objs)
                shared["track_score"] = score
                shared["track_locked"] = locked
            except Exception as e:
                print(f"[infer] {e}")
            finally:
                shared["busy"] = False
                shared["last_infer"] = time.time()

    infer_q = queue.Queue()
    threading.Thread(target=inference_worker, daemon=True, name="infer-worker").start()

    while True:
        now = time.time()
        motion = read_motion()

        # 网络辅助视觉：每 5 秒问一次塔台无人机是否在线，据此开关无人机专项模式
        if now - last_uav_check >= 5.0:
            last_uav_check = now
            online = uav_online_from_tower()
            if online is not None and online != shared["drone_mode"]:
                shared["drone_mode"] = online
                print(f"{time.strftime('%H:%M:%S')} 网络侧 Tello "
                      f"{'在线' if online else '离线'} → 无人机专项门槛 "
                      f"{'conf>=' + str(CONF_DRONE) if online else 'conf>=' + str(CONF_GENERAL) + '(常规)'}")

        # 周期性全量 GC：兜底回收引用循环（ultralytics Results 等对象图）
        if now - last_gc >= 60:
            gc.collect()
            last_gc = now

        # 新帧 → 立即画最近一次的框并推流（不等推理）
        mt = frame_mtime()
        if mt > last_frame_mtime:
            last_frame_mtime = mt
            fps_count += 1
            if now - fps_t0 >= 5.0:
                print(f"{time.strftime('%H:%M:%S')} 推流帧率 {fps_count / (now - fps_t0):.1f} fps")
                fps_count = 0
                fps_t0 = now
            try:
                frame = read_color()
                if frame is not None:
                    # 推流前再跑一遍 hold，保证 annotated 在漏检帧仍画橙色框
                    objs_draw = shared["objects"]
                    if not any((o.get("det_src") == "depthflow") for o in (objs_draw or [])):
                        shared["objects"] = apply_drone_hold(objs_draw)
                    push_frame(draw(frame, shared["objects"]))
                    if not shared["busy"] and now - shared["last_infer"] >= INFER_INTERVAL:
                        shared["busy"] = True
                        infer_q.put_nowait(1)
            except Exception as e:
                print(f"[skip] {e}")
                time.sleep(0.05)
                continue

        # 固定节奏上报变化度 + 物体坐标
        if now - last_report >= args.interval:
            last_report = now
            if not any((o.get("det_src") == "depthflow") for o in (shared["objects"] or [])):
                shared["objects"] = apply_drone_hold(shared["objects"])
            objects = shared["objects"]
            payload = {
                "source": "kinect-yolo3d",
                "change": {
                    "changed": bool(motion.get("changed")),
                    "ratio": motion.get("ratio"),
                    "mean_mm": motion.get("mean_mm"),
                    "level": change_level(motion.get("ratio"), motion.get("changed")),
                },
                "objects": [
                    {"label": o["label"], "conf": o["conf"],
                     "x": o["x"], "y": o["y"], "z": o["z"],
                     "drone": o.get("drone"), "verified": o.get("verified"), "track_score": o.get("track_score"), "track_locked": o.get("track_locked"), "track_mode": o.get("track_mode"), "det_src": o.get("det_src"),
                     "phys_m": list(o["phys"]) if o.get("phys") else None,
                     "held": bool(o.get("held")),
                     "promoted_from": o.get("promoted_from")}
                    for o in objects
                ],
                "drone_mode": shared["drone_mode"],
            }
            try:
                r = SESSION.post(REPORT, json=payload, timeout=3)
                if r.status_code != 200:
                    print(f"[report] HTTP {r.status_code}")
                r.content
            except Exception as e:
                print(f"[report] {e}")

            reports += 1
            if reports % 5 == 0:
                ratio = motion.get("ratio")
                ratio_txt = f"{ratio * 100:.1f}%" if isinstance(ratio, (int, float)) else "—"
                print(f"{time.strftime('%H:%M:%S')} 变化度 {payload['change']['level']}"
                      f"（{ratio_txt}） · 对象 {len(objects)}")

        time.sleep(0.005)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止")
        sys.exit(0)
