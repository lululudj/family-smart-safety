# -*- coding: utf-8 -*-
"""帧来源：Kinect 运行时目录、离线序列、以及无硬件的合成房间。

实时扫描不打开 Kinect 设备。``kinect_direct.py`` 已经独占传感器，
并把配准后的彩色 / 深度写到运行时目录。这里只读那些文件，
因此悬停和检测可以照常跑。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

import cv2
import numpy as np

from room_scan.intrinsics import (
    Intrinsics,
    intrinsics_from_meta,
    kinect_v2_intrinsics,
    load_intrinsics_json,
    save_intrinsics_json,
)

COLOR_NAME = "color.jpg"
DEPTH_NAME = "depth_raw.bin"
META_NAME = "depth_meta.json"


@dataclass
class Frame:
    """一帧已对齐的 RGB-D。color 为 RGB uint8，depth 为毫米 uint16，0 表示无效。"""

    color_rgb: np.ndarray
    depth_mm: np.ndarray
    stamp: float = 0.0


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_kinect_dir() -> str:
    """与 kinect_direct.py 相同：环境变量 KINECT_DIR，否则 tower_ui/kinect_runtime。"""
    env = os.environ.get("KINECT_DIR")
    if env:
        return env
    return os.path.join(project_root(), "tower_ui", "kinect_runtime")


def _read_bytes(path: str, retries: int = 4, delay: float = 0.01) -> np.ndarray | None:
    """短重试读取。kinect_direct 用 os.replace 换文件，撞上替换会 PermissionError。"""
    last = None
    for _ in range(retries):
        try:
            return np.fromfile(path, dtype=np.uint8)
        except OSError as exc:
            last = exc
            time.sleep(delay)
    if last is not None:
        return None
    return None


def _write_image(path: str, image: np.ndarray) -> None:
    """用 imencode + tofile，避开 OpenCV 在中文路径上 imwrite 失败的问题。"""
    ext = os.path.splitext(path)[1] or ".png"
    ok, buf = cv2.imencode(ext, image)
    if not ok:
        raise RuntimeError(f"编码图像失败: {path}")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    buf.tofile(path)


def _read_image(path: str, flags: int) -> np.ndarray | None:
    data = _read_bytes(path)
    if data is None or data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def densify_depth(depth: np.ndarray, k: int = 3, max_std_mm: float = 40.0,
                  min_count: int = 3, passes: int = 2) -> np.ndarray:
    """填上 Kinect 配准深度里的小空洞，但不跨过深度跳变，也不填大片缺失。

    ``kinect_direct`` 把 512×424 的深度点撒到 960×540 上，像素之间常有 1 像素缝。
    里程计要在邻域里估法向，这种缝会把跟踪打散。这里只用局部一致的邻居填缝：
    标准差超过 ``max_std_mm`` 的地方视为物体边缘，保持为 0。两轮 3×3 大约填 2 像素，
    玻璃、镜子那种大洞会留下来。
    """
    out = np.array(depth, dtype=np.uint16, copy=True)
    kernel = np.ones((k, k), np.float32)
    for _ in range(max(1, passes)):
        valid = (out > 0).astype(np.float32)
        values = out.astype(np.float32)
        count = cv2.filter2D(valid, -1, kernel, borderType=cv2.BORDER_CONSTANT)
        total = cv2.filter2D(values * valid, -1, kernel, borderType=cv2.BORDER_CONSTANT)
        total_sq = cv2.filter2D((values * values) * valid, -1, kernel,
                                 borderType=cv2.BORDER_CONSTANT)
        mean = np.divide(total, count, out=np.zeros_like(total), where=count > 0)
        var = np.divide(total_sq, count, out=np.zeros_like(total_sq), where=count > 0)
        var = np.maximum(var - mean * mean, 0.0)
        holes = (out == 0) & (count >= min_count) & (np.sqrt(var) <= max_std_mm)
        filled = out.copy()
        filled[holes] = np.clip(mean[holes], 1, 65535).astype(np.uint16)
        out = filled
    return out


class RuntimeFrameSource:
    """读取 kinect_direct 正在写的 color.jpg / depth_raw.bin / depth_meta.json。"""

    def __init__(self, directory: str | None = None):
        self.directory = directory or default_kinect_dir()
        self.color_path = os.path.join(self.directory, COLOR_NAME)
        self.depth_path = os.path.join(self.directory, DEPTH_NAME)
        self.meta_path = os.path.join(self.directory, META_NAME)
        self.last_error = ""
        self._sig_seen = None

    def load_intrinsics(self) -> Intrinsics:
        if os.path.isfile(self.meta_path):
            try:
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                return intrinsics_from_meta(meta)
            except (OSError, ValueError, json.JSONDecodeError):
                self.last_error = "depth_meta.json 无法解析，改用 Kinect v2 默认内参"
        return kinect_v2_intrinsics()

    def _signature(self):
        def one(path):
            st = os.stat(path)
            return (st.st_mtime_ns, st.st_size)
        return (one(self.color_path), one(self.depth_path))

    def read_if_new(self) -> Frame | None:
        if not (os.path.isfile(self.color_path) and os.path.isfile(self.depth_path)):
            self.last_error = (
                f"还没有彩色/深度文件。请先运行 python kinect_direct.py ，目录 {self.directory}")
            return None
        try:
            sig_before = self._signature()
        except OSError as exc:
            self.last_error = str(exc)
            return None
        if sig_before == self._sig_seen:
            return None
        frame = self._read_frame(sig_before)
        if frame is None:
            return None
        try:
            sig_after = self._signature()
        except OSError:
            return None
        if sig_after != sig_before:
            # 读的过程中文件又被换掉了，这一帧可能撕掉，下一圈再读。
            self.last_error = "帧正在写入，跳过"
            return None
        self._sig_seen = sig_after
        self.last_error = ""
        return frame

    def _read_frame(self, sig) -> Frame | None:
        intr = self.load_intrinsics()
        color_bytes = _read_bytes(self.color_path)
        depth_bytes = _read_bytes(self.depth_path)
        if color_bytes is None or depth_bytes is None:
            self.last_error = "读取彩色或深度失败（文件可能正在替换）"
            return None
        color_bgr = cv2.imdecode(color_bytes, cv2.IMREAD_COLOR)
        if color_bgr is None:
            self.last_error = "彩色帧解码失败"
            return None
        expect = int(intr.width) * int(intr.height) * 2
        if depth_bytes.size != expect:
            self.last_error = (
                f"深度字节数 {depth_bytes.size} 与内参 {intr.width}×{intr.height} 不符")
            return None
        depth = depth_bytes.view("<u2").reshape((intr.height, intr.width))
        if color_bgr.shape[1] != intr.width or color_bgr.shape[0] != intr.height:
            color_bgr = cv2.resize(color_bgr, (intr.width, intr.height),
                                    interpolation=cv2.INTER_AREA)
        color = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2RGB)
        stamp = sig[1][0] / 1e9
        return Frame(np.ascontiguousarray(color), np.ascontiguousarray(depth), stamp)


class SequenceSource:
    """离线序列目录。

    布局::

        seq/intrinsics.json
        seq/color/000000.png
        seq/depth/000000.png   # uint16，毫米，0 为无效
    """

    def __init__(self, directory: str):
        self.directory = directory
        meta = os.path.join(directory, "intrinsics.json")
        if not os.path.isfile(meta):
            raise FileNotFoundError(f"缺少 {meta}")
        self.intrinsics = load_intrinsics_json(meta)
        color_dir = os.path.join(directory, "color")
        depth_dir = os.path.join(directory, "depth")
        if not os.path.isdir(color_dir) or not os.path.isdir(depth_dir):
            raise FileNotFoundError(f"{directory} 里需要 color/ 和 depth/ 两个子目录")
        stems = []
        for name in sorted(os.listdir(color_dir)):
            stem, _ext = os.path.splitext(name)
            depth_path = _find_depth(depth_dir, stem)
            if depth_path is None:
                continue
            stems.append((os.path.join(color_dir, name), depth_path))
        if not stems:
            raise FileNotFoundError(f"{directory} 里没有成对的 color/ 与 depth/")
        self.pairs = stems
        self.index = 0

    def __len__(self) -> int:
        return len(self.pairs)

    def __iter__(self):
        for i in range(len(self.pairs)):
            frame = self._load(i)
            if frame is not None:
                yield frame

    def read_if_new(self) -> Frame | None:
        if self.index >= len(self.pairs):
            return None
        frame = self._load(self.index)
        self.index += 1
        return frame

    def _load(self, index: int) -> Frame | None:
        color_path, depth_path = self.pairs[index]
        color_bgr = _read_image(color_path, cv2.IMREAD_COLOR)
        depth = _read_image(depth_path, cv2.IMREAD_UNCHANGED)
        if color_bgr is None or depth is None:
            return None
        if depth.ndim == 3:
            depth = depth[:, :, 0]
        depth = depth.astype(np.uint16, copy=False)
        color = cv2.cvtColor(color_bgr, cv2.COLOR_BGR2RGB)
        return Frame(np.ascontiguousarray(color), np.ascontiguousarray(depth), float(index))


def _find_depth(depth_dir: str, stem: str) -> str | None:
    for ext in (".png", ".tif", ".tiff"):
        path = os.path.join(depth_dir, stem + ext)
        if os.path.isfile(path):
            return path
    return None


class SequenceWriter:
    def __init__(self, directory: str, intrinsics: Intrinsics):
        self.directory = directory
        self.color_dir = os.path.join(directory, "color")
        self.depth_dir = os.path.join(directory, "depth")
        os.makedirs(self.color_dir, exist_ok=True)
        os.makedirs(self.depth_dir, exist_ok=True)
        save_intrinsics_json(os.path.join(directory, "intrinsics.json"), intrinsics)
        self.count = 0

    def add(self, frame: Frame) -> None:
        stem = f"{self.count:06d}"
        bgr = cv2.cvtColor(frame.color_rgb, cv2.COLOR_RGB2BGR)
        _write_image(os.path.join(self.color_dir, stem + ".png"), bgr)
        depth = np.ascontiguousarray(frame.depth_mm.astype(np.uint16))
        _write_image(os.path.join(self.depth_dir, stem + ".png"), depth)
        self.count += 1


def camera_pose(index: int, step_m: float = 0.04, yaw_step_deg: float = 0.6,
                forward_m: float = 0.008) -> np.ndarray:
    """合成轨迹：相机在房间里缓慢向右平移并略微偏航。返回相机到世界的 4×4。"""
    yaw = np.deg2rad(yaw_step_deg * index)
    c, s = np.cos(yaw), np.sin(yaw)
    # 绕 Kinect 的 +Y（向下）旋转，正角度让光轴向 +X 偏。
    pose = np.eye(4, dtype=np.float64)
    pose[:3, :3] = np.array([[c, 0.0, s],
                              [0.0, 1.0, 0.0],
                              [-s, 0.0, c]], dtype=np.float64)
    pose[:3, 3] = (index * step_m, 0.0, index * forward_m)
    return pose


# 轴对齐房间 + 两件家具。每项：(轴, 平面坐标, 另两轴的范围, 基色)。
# 轴 0/1/2 = X/Y/Z。Y 向下，与 Kinect 相机一致。
_ROOM_PLANES = (
    (0, -2.0, -1.2, 1.4, -1.0, 3.8),
    (0, 2.0, -1.2, 1.4, -1.0, 3.8),
    (1, -1.2, -2.0, 2.0, -1.0, 3.8),
    (1, 1.4, -2.0, 2.0, -1.0, 3.8),
    (2, 3.8, -2.0, 2.0, -1.2, 1.4),
    (2, -1.0, -2.0, 2.0, -1.2, 1.4),
    (0, 0.30, 0.15, 1.35, 1.6, 2.3),
    (0, 0.90, 0.15, 1.35, 1.6, 2.3),
    (1, 0.15, 0.30, 0.90, 1.6, 2.3),
    (2, 1.60, 0.30, 0.90, 0.15, 1.35),
    (2, 2.30, 0.30, 0.90, 0.15, 1.35),
    (0, -0.90, -0.2, 1.2, 1.2, 1.7),
    (0, -0.40, -0.2, 1.2, 1.2, 1.7),
    (1, -0.20, -0.90, -0.40, 1.2, 1.7),
    (2, 1.20, -0.90, -0.40, -0.2, 1.2),
    (2, 1.70, -0.90, -0.40, -0.2, 1.2),
)


def render_room(pose: np.ndarray, intr: Intrinsics, noise_mm: float = 5.0,
                seed: int = 0, splat: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """把合成房间画成一帧 RGB-D。

    纹理用世界坐标的非周期色块。周期棋盘会让光度里程计跳到相邻格子上，
    跟踪会稳定地偏掉十几厘米；真实房间的家具纹理也不是重复条纹。
    ``splat=True`` 时抽掉约一半像素，模仿 kinect_direct 配准后的缝。
    """
    height, width = int(intr.height), int(intr.width)
    rot = pose[:3, :3]
    origin = pose[:3, 3]
    uu, vv = np.meshgrid(np.arange(width, dtype=np.float64),
                          np.arange(height, dtype=np.float64))
    dirs = np.stack(((uu - intr.cx) / intr.fx,
                     (vv - intr.cy) / intr.fy,
                     np.ones_like(uu)), axis=-1)
    best_z = np.full((height, width), np.inf, dtype=np.float64)
    points = np.zeros((height, width, 3), dtype=np.float64)
    for axis, value, a0, a1, b0, b1 in _ROOM_PLANES:
        normal_w = np.zeros(3, dtype=np.float64)
        normal_w[axis] = 1.0
        point_w = np.zeros(3, dtype=np.float64)
        point_w[axis] = value
        normal_c = rot.T @ normal_w
        plane_d = float(normal_w @ (point_w - origin))
        denom = dirs @ normal_c
        with np.errstate(divide="ignore", invalid="ignore"):
            depth_z = plane_d / denom
        hit_c = dirs * depth_z[..., None]
        hit_w = (rot @ hit_c.reshape(-1, 3).T).T.reshape(height, width, 3) + origin
        others = [0, 1, 2]
        others.remove(axis)
        i, j = others
        valid = (depth_z > 0.45) & (depth_z < 4.6) & np.isfinite(depth_z)
        valid &= (hit_w[..., i] >= a0) & (hit_w[..., i] <= a1)
        valid &= (hit_w[..., j] >= b0) & (hit_w[..., j] <= b1)
        better = valid & (depth_z < best_z)
        best_z = np.where(better, depth_z, best_z)
        points = np.where(better[..., None], hit_w, points)

    ok = np.isfinite(best_z) & (best_z < 4.6)
    cell = 0.22
    ix = np.floor(points[..., 0] / cell).astype(np.int64)
    iy = np.floor(points[..., 1] / cell).astype(np.int64)
    iz = np.floor(points[..., 2] / cell).astype(np.int64)
    hashed = (ix * 73856093) ^ (iy * 19349663) ^ (iz * 83492791)
    fine = 0.08
    fx = np.floor(points[..., 0] / fine).astype(np.int64)
    fy = np.floor(points[..., 1] / fine).astype(np.int64)
    fz = np.floor(points[..., 2] / fine).astype(np.int64)
    shade = ((((fx * 13) ^ (fy * 29) ^ (fz * 57)) & 31) - 16) * 2
    color = np.zeros((height, width, 3), np.uint8)
    colored = np.stack((
        np.clip((hashed & 255) + shade, 0, 255),
        np.clip(((hashed >> 8) & 255) + shade, 0, 255),
        np.clip(((hashed >> 16) & 255) + shade, 0, 255),
    ), axis=-1).astype(np.uint8)
    color[ok] = colored[ok]

    depth = np.zeros((height, width), np.float32)
    depth[ok] = best_z[ok] * 1000.0
    rng = np.random.default_rng(seed)
    if noise_mm > 0 and int(ok.sum()) > 0:
        depth[ok] += rng.normal(0.0, noise_mm, int(ok.sum())).astype(np.float32)
    if splat:
        keep = np.zeros((height, width), dtype=bool)
        keep[::2, ::2] = True
        keep[1::3, 1::2] = True
        depth[~keep] = 0
    depth_u16 = np.clip(depth, 0, 65535).astype(np.uint16)
    return color, depth_u16


def synthetic_sequence(n_frames: int = 10, step_m: float = 0.04,
                       yaw_step_deg: float = 0.6, noise_mm: float = 5.0,
                       seed: int = 0, intr: Intrinsics | None = None):
    """生成一段可重复的合成扫描。返回 (frames, camera_to_world 列表, 内参)。"""
    intr = intr or kinect_v2_intrinsics()
    frames = []
    poses = []
    n_frames = max(2, int(n_frames))
    for i in range(n_frames):
        pose = camera_pose(i, step_m=step_m, yaw_step_deg=yaw_step_deg)
        color, depth = render_room(pose, intr, noise_mm=noise_mm, seed=seed + i, splat=True)
        frames.append(Frame(color, depth, stamp=float(i)))
        poses.append(pose)
    return frames, poses, intr
