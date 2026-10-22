# -*- coding: utf-8 -*-
"""RGB-D 里程计 + 可扩展 TSDF。

跟踪用 Open3D 的混合光度 / 点到面里程计：当前帧是 source，上一帧是 target，
返回的矩阵把当前相机坐标变到上一相机坐标。相机到世界的位姿按

    T_curr = T_prev @ trans

累乘。融入体积时 Open3D 要的是世界到相机的外参，也就是 T 的逆。
这个方向在合成房间上对过：正面墙会落在 z≈3.8 m 的一层上，反过来就会出现双层墙。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import cv2
import numpy as np

from room_scan.frames import Frame, densify_depth
from room_scan.intrinsics import Intrinsics


def _import_open3d():
    try:
        import open3d as o3d
    except ImportError as exc:
        raise ImportError(
            "缺少 open3d。请先安装：pip install open3d"
        ) from exc
    return o3d


def rotation_deg(matrix: np.ndarray) -> float:
    trace = float(np.trace(matrix[:3, :3]))
    cosine = float(np.clip((trace - 1.0) / 2.0, -1.0, 1.0))
    return float(np.degrees(np.arccos(cosine)))


@dataclass
class TrackResult:
    integrated: bool
    tracked: bool
    lost: bool
    skipped_static: bool
    motion_m: float
    message: str


class RoomScanner:
    """把多帧 RGB-D 融成一块带颜色的 TSDF，再抽出三角网格。"""

    def __init__(self, intrinsics: Intrinsics, voxel: float = 0.02, trunc: float | None = None,
                 depth_min: float = 0.5, depth_max: float = 4.5, min_move: float = 0.03,
                 min_rotation_deg: float = 2.0, downscale: int = 1,
                 odo_iters: tuple[int, ...] = (12, 6, 3)):
        o3d = _import_open3d()
        o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
        self.source_intr = intrinsics
        self.downscale = max(1, int(downscale))
        self.intr = intrinsics.scaled(self.downscale)
        self.voxel = float(voxel)
        self.trunc = float(trunc) if trunc is not None else float(voxel) * 4.0
        if self.trunc < self.voxel:
            self.trunc = self.voxel * 4.0
        self.depth_min = float(depth_min)
        self.depth_max = float(depth_max)
        self.min_move = float(min_move)
        self.min_rotation_deg = float(min_rotation_deg)
        # 单帧相对运动超过这个范围就当成跟踪飞了，不写进体积。
        self.max_motion = 0.45
        self.max_rotation_deg = 25.0
        self.min_valid_ratio = 0.08
        self._o3d = o3d
        self._intrinsic = self.intr.o3d()
        option = o3d.pipelines.odometry.OdometryOption()
        option.depth_diff_max = 0.07
        option.depth_min = self.depth_min
        option.depth_max = self.depth_max
        option.iteration_number_per_pyramid_level = o3d.utility.IntVector(list(odo_iters))
        self._option = option
        self._jacobian = o3d.pipelines.odometry.RGBDOdometryJacobianFromHybridTerm()
        self.volume = None
        self.prev_rgbd = None
        self.prev_rel = np.eye(4, dtype=np.float64)
        self.T = np.eye(4, dtype=np.float64)
        self.poses: list[np.ndarray] = []
        self.integrated = 0
        self.tracked = 0
        self.lost = 0
        self._reset_volume()

    def _reset_volume(self) -> None:
        o3d = self._o3d
        self.volume = o3d.pipelines.integration.ScalableTSDFVolume(
            voxel_length=self.voxel,
            sdf_trunc=self.trunc,
            color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
        )

    def reset(self) -> None:
        """清空体积，下一次融入的帧重新当作原点。"""
        self._reset_volume()
        self.prev_rgbd = None
        self.prev_rel = np.eye(4, dtype=np.float64)
        self.T = np.eye(4, dtype=np.float64)
        self.poses = []
        self.integrated = 0
        self.tracked = 0
        self.lost = 0

    def process(self, frame: Frame, integrate: bool = True) -> TrackResult:
        color, depth = self._prepare(frame)
        valid_ratio = float((depth > 0).mean()) if depth.size else 0.0
        if valid_ratio < self.min_valid_ratio:
            self.lost += 1
            return TrackResult(
                False, False, True, False, 0.0,
                f"有效深度只有 {valid_ratio * 100:.0f}%，跳过")

        rgbd = self._make_rgbd(color, depth)
        if self.prev_rgbd is None:
            if not integrate:
                return TrackResult(False, False, False, False, 0.0, "已暂停，等待开始")
            self.volume.integrate(rgbd, self._intrinsic, np.eye(4, dtype=np.float64))
            self.prev_rgbd = rgbd
            self.prev_rel = np.eye(4, dtype=np.float64)
            self.T = np.eye(4, dtype=np.float64)
            self.poses.append(self.T.copy())
            self.integrated = 1
            self.tracked = 1
            self.lost = 0
            return TrackResult(True, True, False, False, 0.0, "首帧已融入，作为坐标原点")

        ok, trans = self._track(rgbd, self.prev_rel)
        if not ok:
            ok, trans = self._track(rgbd, np.eye(4, dtype=np.float64))
        if not ok or trans is None:
            self.lost += 1
            return TrackResult(
                False, False, True, False, 0.0,
                "跟踪失败。请放慢速度，退回和上一视角重叠的位置；偏离太远就按 R 重置")

        self.T = self.T @ trans
        self.prev_rgbd = rgbd
        self.prev_rel = trans
        self.tracked += 1
        moved, turned = self._since_last_keyframe()
        if not integrate:
            return TrackResult(
                False, True, False, False, moved,
                f"已暂停，未融入（相对上一关键帧 {moved * 100:.1f} cm）")
        if self.integrated > 0 and moved < self.min_move and turned < self.min_rotation_deg:
            return TrackResult(
                False, True, False, True, moved,
                f"位移 {moved * 100:.1f} cm，太小，跳过融合")

        extrinsic = np.linalg.inv(self.T)
        self.volume.integrate(rgbd, self._intrinsic, extrinsic)
        self.poses.append(self.T.copy())
        self.integrated += 1
        return TrackResult(
            True, True, False, False, moved,
            f"已融入，相对上一关键帧 {moved * 100:.1f} cm / {turned:.1f}°")

    def trajectory_length(self) -> float:
        if len(self.poses) < 2:
            return 0.0
        points = np.stack([pose[:3, 3] for pose in self.poses], axis=0)
        return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())

    def extract_mesh(self):
        mesh = self.volume.extract_triangle_mesh()
        return clean_mesh(mesh)

    def extract_cloud(self):
        return self.volume.extract_point_cloud()

    def save(self, path: str) -> dict:
        """写出彩色 PLY、同名 OBJ，以及一份小的 meta json。PLY 才带顶点色。"""
        mesh = self.extract_mesh()
        root, _ext = os.path.splitext(path)
        if not root:
            root = path
        os.makedirs(os.path.dirname(os.path.abspath(root)) or ".", exist_ok=True)
        ply_path = root + ".ply"
        obj_path = root + ".obj"
        meta_path = root + "_meta.json"
        o3d = self._o3d
        wrote_ply = o3d.io.write_triangle_mesh(
            ply_path, mesh, write_ascii=False,
            write_vertex_colors=True, write_vertex_normals=True)
        wrote_obj = o3d.io.write_triangle_mesh(
            obj_path, mesh, write_vertex_colors=True, write_vertex_normals=True)
        if not wrote_ply:
            raise RuntimeError(f"写入 PLY 失败: {ply_path}")
        vertices = int(len(mesh.vertices))
        triangles = int(len(mesh.triangles))
        info = {
            "ply": ply_path,
            "obj": obj_path if wrote_obj else "",
            "vertices": vertices,
            "triangles": triangles,
            "frames_integrated": self.integrated,
            "frames_tracked": self.tracked,
            "frames_lost": self.lost,
            "trajectory_m": round(self.trajectory_length(), 4),
            "voxel_m": self.voxel,
            "trunc_m": self.trunc,
            "depth_min_m": self.depth_min,
            "depth_max_m": self.depth_max,
            "intrinsics": self.intr.to_dict(),
            "note": "顶点颜色在 PLY 里。OBJ 多数软件只保留几何。",
        }
        import json
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        info["meta"] = meta_path
        return info

    def _prepare(self, frame: Frame):
        src_w, src_h = int(self.source_intr.width), int(self.source_intr.height)
        color = np.ascontiguousarray(frame.color_rgb)
        if color.ndim == 2:
            color = cv2.cvtColor(color, cv2.COLOR_GRAY2RGB)
        elif color.shape[2] == 4:
            color = cv2.cvtColor(color, cv2.COLOR_RGBA2RGB)
        if color.shape[1] != src_w or color.shape[0] != src_h:
            color = cv2.resize(color, (src_w, src_h), interpolation=cv2.INTER_AREA)
        depth = np.array(frame.depth_mm, dtype=np.uint16, copy=True)
        if depth.shape != (src_h, src_w):
            depth = cv2.resize(depth, (src_w, src_h), interpolation=cv2.INTER_NEAREST)
        near = int(round(self.depth_min * 1000.0))
        far = int(round(self.depth_max * 1000.0))
        depth[(depth > 0) & ((depth < near) | (depth > far))] = 0
        depth = densify_depth(depth)
        depth[(depth > 0) & ((depth < near) | (depth > far))] = 0
        if self.downscale > 1:
            color = cv2.resize(color, (self.intr.width, self.intr.height),
                                interpolation=cv2.INTER_AREA)
            depth = cv2.resize(depth, (self.intr.width, self.intr.height),
                                interpolation=cv2.INTER_NEAREST)
        return np.ascontiguousarray(color), np.ascontiguousarray(depth)

    def _make_rgbd(self, color: np.ndarray, depth: np.ndarray):
        o3d = self._o3d
        color_img = o3d.geometry.Image(color)
        depth_img = o3d.geometry.Image(depth)
        return o3d.geometry.RGBDImage.create_from_color_and_depth(
            color_img, depth_img,
            depth_scale=1000.0,
            depth_trunc=self.depth_max,
            convert_rgb_to_intensity=False,
        )

    def _track(self, rgbd, init: np.ndarray):
        success, trans, _info = self._o3d.pipelines.odometry.compute_rgbd_odometry(
            rgbd, self.prev_rgbd, self._intrinsic,
            np.ascontiguousarray(init, dtype=np.float64),
            self._jacobian, self._option)
        if not success:
            return False, None
        trans = np.asarray(trans, dtype=np.float64)
        distance = float(np.linalg.norm(trans[:3, 3]))
        angle = rotation_deg(trans)
        if distance > self.max_motion or angle > self.max_rotation_deg:
            return False, None
        return True, trans

    def _since_last_keyframe(self) -> tuple[float, float]:
        if not self.poses:
            return 0.0, 0.0
        delta = np.linalg.inv(self.poses[-1]) @ self.T
        return float(np.linalg.norm(delta[:3, 3])), rotation_deg(delta)


def clean_mesh(mesh, min_triangles: int = 200):
    """去掉退化三角形和很小的漂浮碎片。若整块网格都很小，则原样保留。"""
    mesh.remove_duplicated_vertices()
    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_triangles()
    mesh.remove_unreferenced_vertices()
    if len(mesh.triangles) == 0:
        mesh.compute_vertex_normals()
        return mesh
    _clusters, counts, _areas = mesh.cluster_connected_triangles()
    counts = np.asarray(counts)
    clusters = np.asarray(_clusters)
    if counts.size == 0:
        mesh.compute_vertex_normals()
        return mesh
    small = counts < int(min_triangles)
    if bool(small.any()) and not bool(small.all()):
        mesh.remove_triangles_by_mask(small[clusters])
        mesh.remove_unreferenced_vertices()
    mesh.compute_vertex_normals()
    return mesh
