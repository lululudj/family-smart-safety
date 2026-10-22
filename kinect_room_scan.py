#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kinect v2 房间扫描。

拿着 Kinect 在房间里慢慢走或转动，把多帧配准深度融成一块带颜色的网格。
设备仍由 kinect_direct.py 独占；本程序只读它写出的运行时文件，
不改悬停和检测的默认行为。

用法：
    python kinect_direct.py
    python kinect_room_scan.py
    python kinect_room_scan.py --demo
    python kinect_room_scan.py --sequence scans/seq --out scans/room.ply

没有 Kinect 时用 --demo，或用 --sequence 融合事先存好的 RGB-D。
说明见 room_scan/README.md。
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

HELP = """
操作（焦点在这个终端，或在预览窗口上）：
  空格 / P   暂停或继续融合（暂停时仍跟踪，方便短暂停住）
  R          清空体积，从下一帧重新当原点
  S          立刻保存当前网格
  Q / Esc    停止并保存
  H          再显示一次本说明
扫描时请缓慢移动，画面保持重叠；玻璃和镜子会留下空洞或假面。
""".strip()


def interpret_key(key) -> str | None:
    if key is None:
        return None
    text = str(key)
    if text == " ":
        return "toggle"
    text = text.strip().lower()
    if text in {"q", "quit", "esc", "\x1b"}:
        return "quit"
    if text in {" ", "p", "pause", "toggle"}:
        return "toggle"
    if text in {"r", "reset"}:
        return "reset"
    if text in {"s", "save"}:
        return "save"
    if text in {"h", "help", "?"}:
        return "help"
    return None


class KeyReader:
    """终端单键。Windows 用 msvcrt，类 Unix 的 tty 用 cbreak。管道输入则不抢键。"""

    def __init__(self):
        self._queue: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        if os.name == "nt":
            self._thread = threading.Thread(target=self._win_loop, daemon=True)
            self._thread.start()
        elif sys.stdin.isatty():
            self._thread = threading.Thread(target=self._posix_loop, daemon=True)
            self._thread.start()

    def poll(self) -> list[str]:
        found = []
        while True:
            try:
                found.append(self._queue.get_nowait())
            except queue.Empty:
                return found

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.4)

    def _win_loop(self) -> None:
        import msvcrt
        while not self._stop.is_set():
            if not msvcrt.kbhit():
                time.sleep(0.03)
                continue
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                if msvcrt.kbhit():
                    msvcrt.getwch()
                continue
            self._queue.put(ch)

    def _posix_loop(self) -> None:
        import select
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not self._stop.is_set():
                ready, _, _ = select.select([fd], [], [], 0.05)
                if not ready:
                    continue
                ch = sys.stdin.read(1)
                if ch:
                    self._queue.put(ch)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


class Preview:
    """可选预览。没有图形界面时静默退回终端，不让扫描中断。"""

    def __init__(self, color_window: bool, cloud_window: bool):
        self.color_window = color_window
        self.cloud_window = cloud_window
        self.cv_ok = False
        self.vis = None
        self.cloud = None
        self._added = False
        if color_window:
            self._open_cv()
        if cloud_window:
            self._open_cloud()

    def _open_cv(self) -> None:
        try:
            import cv2
            cv2.namedWindow("room_scan", cv2.WINDOW_NORMAL)
            self.cv_ok = True
        except Exception as exc:
            print(f"彩色预览开不了（{exc}），继续在终端里操作。")
            self.cv_ok = False

    def _open_cloud(self) -> None:
        try:
            import open3d as o3d
            vis = o3d.visualization.Visualizer()
            opened = vis.create_window(window_name="房间扫描", width=960, height=540)
            if not opened:
                raise RuntimeError("create_window 返回失败")
            self.vis = vis
            self.cloud = o3d.geometry.PointCloud()
        except Exception as exc:
            print(f"点云预览开不了（{exc}），只保存最终网格。")
            self.vis = None

    def poll_key(self) -> str | None:
        if not self.cv_ok:
            return None
        try:
            import cv2
            key = cv2.waitKey(1)
        except Exception:
            self.cv_ok = False
            return None
        if key is None or key < 0:
            return None
        key = key & 0xFF
        if key in (0, 255):
            return None
        if key == 27:
            return "esc"
        try:
            return chr(key)
        except ValueError:
            return None

    def show(self, color_rgb, text: str) -> None:
        if not self.cv_ok:
            return
        try:
            import cv2
            view = cv2.cvtColor(color_rgb, cv2.COLOR_RGB2BGR)
            cv2.rectangle(view, (8, 8), (min(view.shape[1] - 8, 920), 48), (0, 0, 0), -1)
            cv2.putText(view, text, (16, 36), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (80, 255, 120), 1, cv2.LINE_AA)
            cv2.imshow("room_scan", view)
        except Exception:
            self.cv_ok = False

    def update_cloud(self, cloud) -> None:
        if self.vis is None or cloud is None:
            return
        try:
            if not self._added:
                self.cloud = cloud
                self.vis.add_geometry(self.cloud)
                self._added = True
            else:
                self.cloud.points = cloud.points
                self.cloud.colors = cloud.colors
                self.vis.update_geometry(self.cloud)
            self.vis.poll_events()
            self.vis.update_renderer()
        except Exception as exc:
            print(f"点云预览中断（{exc}）")
            self.close_cloud()

    def close_cloud(self) -> None:
        if self.vis is not None:
            try:
                self.vis.destroy_window()
            except Exception:
                pass
        self.vis = None

    def close(self) -> None:
        self.close_cloud()
        if self.cv_ok:
            try:
                import cv2
                cv2.destroyAllWindows()
            except Exception:
                pass
        self.cv_ok = False


def default_output_path() -> str:
    stamp = time.strftime("%Y%m%d_%H%M")
    return os.path.join(ROOT, "scans", f"room_{stamp}.ply")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kinect v2 房间扫描：移动相机，融合多视角，导出彩色网格。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python kinect_room_scan.py\n"
            "  python kinect_room_scan.py --demo\n"
            "  python kinect_room_scan.py --sequence scans/seq --out scans/room.ply\n"
            "  python kinect_room_scan.py --voxel 0.03 --depth-min 0.5 --depth-max 4.5\n"
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--demo", action="store_true",
                        help="不用 Kinect，融合一段合成房间序列（给无硬件的机器做冒烟测试）")
    source.add_argument("--sequence", metavar="DIR",
                        help="融合离线 RGB-D 目录（intrinsics.json + color/ + depth/）")
    parser.add_argument("--kinect-dir", default=None,
                        help="kinect_direct 的运行时目录，默认 $KINECT_DIR 或 tower_ui/kinect_runtime")
    parser.add_argument("--out", default=None,
                        help="网格路径，默认 scans/room_YYYYMMDD_HHMM.ply（同时写同名 OBJ）")
    parser.add_argument("--voxel", type=float, default=0.02,
                        help="TSDF 体素边长（米）。家庭房间默认 0.02，大约是 Kinect 的厘米级精度")
    parser.add_argument("--trunc", type=float, default=None,
                        help="TSDF 截断距离（米）。默认 4 倍体素")
    parser.add_argument("--depth-min", type=float, default=0.5,
                        help="最近有效深度（米），默认 0.5")
    parser.add_argument("--depth-max", type=float, default=4.5,
                        help="最远有效深度（米），默认 4.5")
    parser.add_argument("--min-move", type=float, default=0.03,
                        help="相对上一关键帧至少移动这么多米才融入，默认 0.03")
    parser.add_argument("--frames", type=int, default=10,
                        help="--demo 的帧数，默认 10")
    parser.add_argument("--downscale", type=int, default=1,
                        help="处理前的整数降采样。2 表示半分辨率，跟踪更快、细节更少")
    parser.add_argument("--dump", metavar="DIR",
                        help="把读到的原始帧存成离线序列，之后可以用 --sequence 重放")
    parser.add_argument("--stale", type=float, default=2.0,
                        help="超过这么多秒没有新帧就提示检查 kinect_direct，默认 2")
    parser.add_argument("--start-paused", action="store_true",
                        help="启动时先不融合，按 P 或空格再开始")
    preview = parser.add_mutually_exclusive_group()
    preview.add_argument("--preview", action="store_true",
                         help="打开彩色窗口，并尽量打开累积点云窗口")
    preview.add_argument("--no-preview", action="store_true",
                         help="不打开窗口。--demo / --sequence 默认就是这样")
    return parser


def _scanner_from_args(args, intrinsics):
    from room_scan.fusion import RoomScanner
    return RoomScanner(
        intrinsics,
        voxel=args.voxel,
        trunc=args.trunc,
        depth_min=args.depth_min,
        depth_max=args.depth_max,
        min_move=args.min_move,
        downscale=args.downscale,
    )


def _print_scan_header(scanner, source_name: str) -> None:
    intrinsics = scanner.source_intr
    print(f"Kinect 房间扫描  来源: {source_name}")
    print(f"内参 {intrinsics.width}×{intrinsics.height}  "
          f"fx={intrinsics.fx:.3f} fy={intrinsics.fy:.3f}  "
          f"cx={intrinsics.cx:.1f} cy={intrinsics.cy:.1f}")
    if scanner.downscale > 1:
        print(f"处理分辨率 {scanner.intr.width}×{scanner.intr.height}"
              f"（降采样 {scanner.downscale}）")
    print(f"体素 {scanner.voxel:.3f} m，截断 {scanner.trunc:.3f} m，"
          f"深度 {scanner.depth_min:.2f}–{scanner.depth_max:.2f} m，"
          f"关键帧位移 {scanner.min_move:.2f} m")


def run_batch(frames, scanner, out_path: str) -> int:
    for index, frame in enumerate(frames):
        result = scanner.process(frame, integrate=True)
        print(f"帧 {index:03d}  {result.message}")
    return _finish(scanner, out_path)


def _finish(scanner, out_path: str) -> int:
    print(f"融入 {scanner.integrated} 帧，跟踪 {scanner.tracked}，"
          f"丢失 {scanner.lost}，轨迹约 {scanner.trajectory_length():.2f} m")
    if scanner.integrated < 2:
        print("融合帧数不足，没有写出网格。确认画面在动，并且深度不是空的。")
        return 2
    info = scanner.save(out_path)
    if info["vertices"] < 100 or info["triangles"] < 100:
        print(f"网格几乎是空的（顶点 {info['vertices']}，三角形 {info['triangles']}）。")
        return 2
    print(f"彩色网格  {info['ply']}")
    if info.get("obj"):
        print(f"几何 OBJ  {info['obj']}")
    print(f"顶点 {info['vertices']}，三角形 {info['triangles']}，记录 {info['meta']}")
    return 0


def run_live(args, source, scanner, out_path: str, dump_dir: str | None,
             key_reader=None, max_cycles: int | None = None) -> int:
    from room_scan.frames import SequenceWriter
    integrating = not args.start_paused
    keys = key_reader if key_reader is not None else KeyReader()
    own_keys = key_reader is None
    batch = bool(args.demo or args.sequence)
    if args.no_preview or (batch and not args.preview):
        color_window, cloud_window = False, False
    elif args.preview:
        color_window, cloud_window = True, True
    else:
        color_window, cloud_window = True, False
    preview = Preview(color_window, cloud_window)
    writer = None
    if dump_dir:
        writer = SequenceWriter(dump_dir, scanner.source_intr)
        print(f"原始帧将写到 {dump_dir}")
    print(HELP)
    if integrating:
        print("正在融合。慢慢移动 Kinect。")
    else:
        print("已暂停。按 P 或空格开始融合。")
    last_frame_at = time.time()
    warned = False
    cloud_every = 5
    last_cloud_integrated = 0
    cycles = 0
    try:
        while True:
            if max_cycles is not None and cycles >= max_cycles:
                print("达到循环上限，停止。")
                break
            cycles += 1
            actions = [interpret_key(key) for key in keys.poll()]
            window_key = preview.poll_key()
            action = interpret_key(window_key)
            if action:
                actions.append(action)
            if "quit" in actions:
                print("停止。")
                break
            if "help" in actions:
                print(HELP)
            if "toggle" in actions:
                integrating = not integrating
                print("继续融合。" if integrating else "已暂停融合。")
            if "reset" in actions:
                scanner.reset()
                last_cloud_integrated = 0
                print("体积已重置。")
            if "save" in actions:
                if scanner.integrated < 1:
                    print("还没有融入任何帧。")
                else:
                    info = scanner.save(out_path)
                    print(f"已保存 {info['ply']}  顶点 {info['vertices']}")

            frame = source.read_if_new()
            if frame is None:
                if time.time() - last_frame_at >= args.stale and not warned:
                    detail = getattr(source, "last_error", "") or "没有新的一帧"
                    print(f"等待 Kinect 帧：{detail}")
                    warned = True
                if args.sequence and getattr(source, "index", 0) >= len(source):
                    print("序列播放完毕。")
                    break
                time.sleep(0.03)
                continue
            warned = False
            last_frame_at = time.time()
            if writer is not None:
                writer.add(frame)
            result = scanner.process(frame, integrate=integrating)
            if not result.skipped_static:
                xyz = scanner.T[:3, 3]
                state = "融合中" if integrating else "已暂停"
                print(f"[{state}] 融入 {scanner.integrated} 跟踪 {scanner.tracked} "
                      f"丢失 {scanner.lost}  "
                      f"xyz=({xyz[0]:.2f},{xyz[1]:.2f},{xyz[2]:.2f})  {result.message}")
            preview.show(frame.color_rgb, f"fused {scanner.integrated}  lost {scanner.lost}")
            if (preview.vis is not None and result.integrated
                    and scanner.integrated - last_cloud_integrated >= cloud_every):
                preview.update_cloud(scanner.extract_cloud())
                last_cloud_integrated = scanner.integrated
    except KeyboardInterrupt:
        print("\n收到中断，准备保存。")
    finally:
        if own_keys:
            keys.close()
        preview.close()
    return _finish(scanner, out_path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_path = args.out or default_output_path()
    try:
        if args.demo:
            from room_scan.frames import SequenceWriter, synthetic_sequence
            frames, _poses, intrinsics = synthetic_sequence(n_frames=args.frames)
            if args.dump:
                writer = SequenceWriter(args.dump, intrinsics)
                for frame in frames:
                    writer.add(frame)
                print(f"已把 {writer.count} 帧写到 {args.dump}")
            scanner = _scanner_from_args(args, intrinsics)
            _print_scan_header(scanner, "合成房间（--demo）")
            return run_batch(frames, scanner, out_path)

        if args.sequence:
            from room_scan.frames import SequenceSource
            source = SequenceSource(args.sequence)
            scanner = _scanner_from_args(args, source.intrinsics)
            _print_scan_header(scanner, args.sequence)
            return run_batch(source, scanner, out_path)

        from room_scan.frames import RuntimeFrameSource
        source = RuntimeFrameSource(args.kinect_dir)
        intrinsics = source.load_intrinsics()
        scanner = _scanner_from_args(args, intrinsics)
        _print_scan_header(scanner, source.directory)
        if source.last_error:
            print(source.last_error)
        return run_live(args, source, scanner, out_path, args.dump)
    except ImportError as exc:
        print(str(exc))
        return 1
    except (OSError, FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"扫描失败: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
