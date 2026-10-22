# -*- coding: utf-8 -*-
"""无硬件冒烟测试：合成多视角必须被融进同一块彩色网格，而不是只导出一帧点云。"""

from __future__ import annotations

import os
import tempfile
import unittest

import cv2
import numpy as np

from room_scan.frames import (
    RuntimeFrameSource,
    SequenceSource,
    SequenceWriter,
    densify_depth,
    synthetic_sequence,
)
from room_scan.fusion import RoomScanner
from room_scan.intrinsics import kinect_v2_intrinsics


def _front_wall_std(mesh) -> float:
    vertices = np.asarray(mesh.vertices)
    mask = (np.abs(vertices[:, 0]) < 0.3) & (np.abs(vertices[:, 1]) < 0.3) & (vertices[:, 2] > 2.5)
    if int(mask.sum()) < 30:
        return 99.0
    return float(vertices[mask, 2].std())


class DensifyTest(unittest.TestCase):
    def test_small_gaps_fill_large_holes_stay(self):
        depth = np.zeros((40, 40), np.uint16)
        depth[:, :5] = 2000
        filled = densify_depth(depth, passes=2)
        self.assertGreater(int(filled[20, 6]), 0)
        self.assertEqual(int(filled[20, 30]), 0)
        self.assertEqual(int(filled[20, 2]), 2000)


class RuntimeLayoutTest(unittest.TestCase):
    def test_reads_kinect_direct_files(self):
        intr = kinect_v2_intrinsics()
        with tempfile.TemporaryDirectory() as folder:
            color = np.zeros((intr.height, intr.width, 3), np.uint8)
            color[:, :, 1] = 180
            ok, buf = cv2.imencode(".jpg", color)
            self.assertTrue(ok)
            buf.tofile(os.path.join(folder, "color.jpg"))
            depth = np.full((intr.height, intr.width), 1500, np.uint16)
            depth.tofile(os.path.join(folder, "depth_raw.bin"))
            with open(os.path.join(folder, "depth_meta.json"), "w", encoding="utf-8") as f:
                f.write(
                    '{"width":960,"height":540,"unit":"mm","fx":532.159,'
                    '"fy":532.199,"cx":479.5,"cy":269.5}'
                )
            source = RuntimeFrameSource(folder)
            loaded = source.load_intrinsics()
            self.assertAlmostEqual(loaded.fx, 532.159, places=3)
            frame = source.read_if_new()
            self.assertIsNotNone(frame)
            self.assertEqual(frame.depth_mm.shape, (540, 960))
            self.assertEqual(int(frame.depth_mm[10, 10]), 1500)
            self.assertEqual(int(frame.color_rgb[10, 10, 1]), 180)
            self.assertIsNone(source.read_if_new())


class FusionDemoTest(unittest.TestCase):
    def test_multiview_colored_mesh(self):
        frames, poses, intr = synthetic_sequence(n_frames=6, seed=3)
        scanner = RoomScanner(intr, voxel=0.03, trunc=0.12, min_move=0.02)
        single = RoomScanner(intr, voxel=0.03, trunc=0.12)
        single.process(frames[0], integrate=True)
        for frame in frames:
            result = scanner.process(frame, integrate=True)
            self.assertFalse(result.lost, result.message)
        self.assertGreaterEqual(scanner.integrated, 5)
        self.assertGreater(scanner.trajectory_length(), 0.15)
        endpoint = np.linalg.norm(scanner.T[:3, 3] - poses[-1][:3, 3])
        self.assertLess(endpoint, 0.05)

        mesh = scanner.extract_mesh()
        alone = single.extract_mesh()
        self.assertGreater(len(mesh.vertices), 1000)
        self.assertGreater(len(mesh.triangles), 1000)
        self.assertGreater(len(alone.vertices), 1000)
        # 多视角会把单帧厚壳削薄，顶点不一定更多。用轨迹、终点误差和墙面厚度证明
        # 这几帧进的是同一个体积，而不是各存各的。
        positions = np.stack([pose[:3, 3] for pose in scanner.poses], axis=0)
        self.assertGreater(float(positions[:, 0].max() - positions[:, 0].min()), 0.15)
        colors = np.asarray(mesh.vertex_colors)
        self.assertGreater(float(colors.mean()), 0.05)
        self.assertLess(_front_wall_std(mesh), 0.03)
        extent = np.asarray(mesh.vertices)
        self.assertGreater(float(extent[:, 0].max() - extent[:, 0].min()), 3.5)

        with tempfile.TemporaryDirectory() as folder:
            info = scanner.save(os.path.join(folder, "room.ply"))
            self.assertGreater(os.path.getsize(info["ply"]), 1000)
            self.assertTrue(os.path.isfile(info["obj"]))
            import open3d as o3d
            loaded = o3d.io.read_triangle_mesh(info["ply"])
            self.assertGreater(len(loaded.vertices), 1000)
            self.assertGreater(float(np.asarray(loaded.vertex_colors).mean()), 0.05)

    def test_downscale_keeps_intrinsics_consistent(self):
        frames, _poses, intr = synthetic_sequence(n_frames=2, seed=0)
        scanner = RoomScanner(intr, voxel=0.05, trunc=0.2, downscale=2)
        result = scanner.process(frames[0], integrate=True)
        self.assertTrue(result.integrated)
        self.assertEqual(scanner.intr.width, 480)
        self.assertEqual(scanner.intr.height, 270)
        self.assertAlmostEqual(scanner.intr.fx, intr.fx / 2.0, places=3)

    def test_pause_does_not_integrate(self):
        frames, _poses, intr = synthetic_sequence(n_frames=3, seed=1)
        scanner = RoomScanner(intr, voxel=0.04, trunc=0.16, min_move=0.02)
        first = scanner.process(frames[0], integrate=True)
        self.assertTrue(first.integrated)
        held = scanner.process(frames[1], integrate=False)
        self.assertTrue(held.tracked)
        self.assertFalse(held.integrated)
        self.assertEqual(scanner.integrated, 1)
        resumed = scanner.process(frames[2], integrate=True)
        self.assertTrue(resumed.integrated)
        self.assertEqual(scanner.integrated, 2)

    def test_sequence_roundtrip(self):
        frames, _poses, intr = synthetic_sequence(n_frames=4, seed=7)
        with tempfile.TemporaryDirectory() as folder:
            writer = SequenceWriter(folder, intr)
            for frame in frames:
                writer.add(frame)
            source = SequenceSource(folder)
            self.assertEqual(len(source), 4)
            scanner = RoomScanner(source.intrinsics, voxel=0.04, trunc=0.16, min_move=0.02)
            for frame in source:
                result = scanner.process(frame, integrate=True)
                self.assertFalse(result.lost, result.message)
            self.assertGreaterEqual(scanner.integrated, 3)
            info = scanner.save(os.path.join(folder, "replay.ply"))
            self.assertGreater(info["vertices"], 500)


class LiveControlTest(unittest.TestCase):
    def test_pause_save_and_quit(self):
        from kinect_room_scan import run_live

        frames, _poses, intr = synthetic_sequence(n_frames=4, seed=4)
        scanner = RoomScanner(intr, voxel=0.04, trunc=0.16, min_move=0.02)

        class ListSource:
            def __init__(self):
                self.i = 0
                self.last_error = ""

            def read_if_new(self):
                if self.i >= len(frames):
                    return None
                frame = frames[self.i]
                self.i += 1
                return frame

        class ScriptKeys:
            def __init__(self):
                self.batches = [[], [], ["p"], ["s"], ["q"]]

            def poll(self):
                if not self.batches:
                    return ["q"]
                return self.batches.pop(0)

            def close(self):
                return None

        class Args:
            start_paused = False
            no_preview = True
            preview = False
            demo = False
            sequence = None
            stale = 30.0

        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, "live.ply")
            code = run_live(
                Args(), ListSource(), scanner, out, None,
                key_reader=ScriptKeys(), max_cycles=8)
            self.assertEqual(code, 0)
            self.assertEqual(scanner.integrated, 2)
            self.assertTrue(os.path.isfile(out))
            self.assertGreater(os.path.getsize(out), 1000)


class KeyParseTest(unittest.TestCase):
    def test_controls(self):
        from kinect_room_scan import interpret_key
        self.assertEqual(interpret_key("p"), "toggle")
        self.assertEqual(interpret_key(" "), "toggle")
        self.assertEqual(interpret_key("R"), "reset")
        self.assertEqual(interpret_key("s"), "save")
        self.assertEqual(interpret_key("esc"), "quit")
        self.assertEqual(interpret_key("\x1b"), "quit")
        self.assertIsNone(interpret_key("x"))


if __name__ == "__main__":
    unittest.main()
