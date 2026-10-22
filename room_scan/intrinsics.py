# -*- coding: utf-8 -*-
"""相机内参。

真机默认值与 ``kinect_direct.py`` 写出的 960×540 配准深度一致
（fx≈532.159，水平视场约 84.1°）。扫描时若目录里已有
``depth_meta.json``，以文件为准，不在这里另起一套标定。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass


# 与 kinect_direct.FX/FY/CX/CY、OUT_W/OUT_H 保持同一组数字。
DEFAULT_WIDTH = 960
DEFAULT_HEIGHT = 540
DEFAULT_FX = 532.159
DEFAULT_FY = 532.199
DEFAULT_CX = 479.5
DEFAULT_CY = 269.5


@dataclass
class Intrinsics:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float

    def scaled(self, downscale: int) -> "Intrinsics":
        """整数降采样后的内参。downscale=2 时宽高与焦距、主点都折半。"""
        n = max(1, int(downscale))
        if n == 1:
            return self
        return Intrinsics(
            width=int(self.width) // n,
            height=int(self.height) // n,
            fx=float(self.fx) / n,
            fy=float(self.fy) / n,
            cx=float(self.cx) / n,
            cy=float(self.cy) / n,
        )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["depth_unit"] = "mm"
        data["depth_scale"] = 1000.0
        return data

    def o3d(self):
        import open3d as o3d

        return o3d.camera.PinholeCameraIntrinsic(
            int(self.width), int(self.height),
            float(self.fx), float(self.fy), float(self.cx), float(self.cy))


def kinect_v2_intrinsics() -> Intrinsics:
    return Intrinsics(
        DEFAULT_WIDTH, DEFAULT_HEIGHT,
        DEFAULT_FX, DEFAULT_FY, DEFAULT_CX, DEFAULT_CY)


def intrinsics_from_meta(meta: dict, fallback: Intrinsics | None = None) -> Intrinsics:
    base = fallback or kinect_v2_intrinsics()
    return Intrinsics(
        width=int(meta.get("width", base.width)),
        height=int(meta.get("height", base.height)),
        fx=float(meta.get("fx", base.fx)),
        fy=float(meta.get("fy", base.fy)),
        cx=float(meta.get("cx", base.cx)),
        cy=float(meta.get("cy", base.cy)),
    )


def load_intrinsics_json(path: str, fallback: Intrinsics | None = None) -> Intrinsics:
    with open(path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return intrinsics_from_meta(meta, fallback)


def save_intrinsics_json(path: str, intr: Intrinsics) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(intr.to_dict(), f, ensure_ascii=False, indent=2)
