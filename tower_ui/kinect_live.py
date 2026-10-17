#!/usr/bin/env python3
"""Kinect v2 live MJPEG service with isolated capture process."""

import json
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from multiprocessing import Process
from socketserver import ThreadingMixIn

os.environ.setdefault("DISPLAY", ":0")

import cv2

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kinect_runtime")
COLOR = ROOT + "/color.jpg"
DEPTH = ROOT + "/depth.jpg"
IR = ROOT + "/ir.jpg"
STATUS = ROOT + "/status.json"
SCAN_CONTROL = ROOT + "/scan_control.json"
running = True


def atomic_write(path, data):
    temp = path + ".tmp"
    with open(temp, "wb") as handle:
        handle.write(data)
    os.replace(temp, path)


def encode(image):
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return buf.tobytes() if ok else b""


def write_status(**values):
    atomic_write(STATUS, json.dumps(values).encode())


def read_scan_control():
    try:
        with open(SCAN_CONTROL, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return {"active": False}


def capture_worker():
    import numpy as np
    import pylibfreenect2 as lf2

    try:
        os.makedirs(ROOT, exist_ok=True)
        pipeline = lf2.CpuPacketPipeline()
        freenect = lf2.Freenect2()
        if freenect.enumerateDevices() == 0:
            write_status(ok=False, running=False, frames=0, error="no Kinect v2 found")
            return

        serial = freenect.getDeviceSerialNumber(0)
        device = freenect.openDefaultDevice(pipeline)
        types = lf2.FrameType.Color | lf2.FrameType.Ir | lf2.FrameType.Depth
        listener = lf2.SyncMultiFrameListener(types)
        device.setColorFrameListener(listener)
        device.setIrAndDepthFrameListener(listener)
        device.start()
        frames_seen = 0
        scan_seen = 0
        last_scan_write = 0.0
        write_status(ok=True, running=True, frames=0, serial=str(serial), error="")

        while True:
            frames = listener.waitForNewFrame()
            try:
                color = frames["color"].asarray()[:, :, :3].copy()
                ir = frames["ir"].asarray()
                depth = frames["depth"].asarray().astype(np.float32)

                scan = read_scan_control()
                if scan.get("active") and time.time() - last_scan_write >= 0.2:
                    scan_dir = scan.get("directory")
                    if scan_dir:
                        os.makedirs(scan_dir, exist_ok=True)
                        scan_seen += 1
                        stem = f"frame_{scan_seen:06d}"
                        cv2.imwrite(os.path.join(scan_dir, stem + "_color.jpg"), color, [cv2.IMWRITE_JPEG_QUALITY, 90])
                        raw_depth = frames["depth"].asarray().copy()
                        cv2.imwrite(os.path.join(scan_dir, stem + "_depth.png"), raw_depth)
                        with open(os.path.join(scan_dir, stem + ".json"), "w", encoding="utf-8") as meta:
                            json.dump({"frame": scan_seen, "timestamp": time.time(), "depth_unit": "millimetres"}, meta)
                        last_scan_write = time.time()

                ir8 = cv2.normalize(ir, None, 0, 255, cv2.NORM_MINMAX).astype("uint8")
                ir8 = cv2.applyColorMap(ir8, cv2.COLORMAP_BONE)
                depth[depth == 0] = np.nan
                valid = ~np.isnan(depth)
                if valid.sum() > 100:
                    lo = np.nanpercentile(depth, 2)
                    hi = np.nanpercentile(depth, 98)
                    depth8 = np.clip((depth - lo) / max(hi - lo, 1) * 255, 0, 255).astype("uint8")
                    depth8[~valid] = 0
                else:
                    depth8 = np.zeros_like(depth, dtype="uint8")
                depth8 = cv2.applyColorMap(depth8, cv2.COLORMAP_JET)

                atomic_write(COLOR, encode(color))
                atomic_write(DEPTH, encode(depth8))
                atomic_write(IR, encode(ir8))
                frames_seen += 1
                if frames_seen % 5 == 0:
                    write_status(ok=True, running=True, frames=frames_seen, serial=str(serial), error="")
            finally:
                listener.release(frames)
    except Exception as exc:
        write_status(ok=False, running=False, frames=0, error=str(exc))
        raise


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        paths = {"/color.mjpeg": COLOR, "/depth.mjpeg": DEPTH, "/ir.mjpeg": IR}
        if self.path in paths:
            self.stream(paths[self.path])
        elif self.path == "/api/status":
            try:
                body = open(STATUS, "rb").read()
            except FileNotFoundError:
                body = json.dumps({"ok": False, "running": False, "frames": 0, "error": "starting"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def stream(self, path):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            while running:
                try:
                    data = open(path, "rb").read()
                except FileNotFoundError:
                    data = b""
                if data:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n")
                time.sleep(0.033)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_):
        pass


def main():
    global running
    os.makedirs(ROOT, exist_ok=True)
    capture = Process(target=capture_worker, daemon=True)
    capture.start()
    server = ThreadedHTTPServer(("0.0.0.0", int(os.environ.get("KINECT_PORT", "8081"))), Handler)

    def stop(*_):
        running = False
        capture.terminate()
        server.shutdown()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.serve_forever()


if __name__ == "__main__":
    main()

