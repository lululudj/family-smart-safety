# -*- coding: utf-8 -*-
"""pykinect2 在 64 位 Python 3.12 上的兼容性补丁。

上游 pykinect2 0.1.0 是针对老环境写的，直接导入会连撞三处：
  1. PyKinectV2.py: assert sizeof(tagSTATSTG) == 72
     72 是 32 位下的结构大小；64 位 Python 上 tagSTATSTG 实际 80 字节。
  2. PyKinectV2.py: from comtypes import _check_version; _check_version('')
     comtypes 新版没有这个签名，传空串直接 ImportError("Wrong version")。
  3. 源文件头声明 `coding: mbcs` —— 任何按 UTF-8 写回的操作都会把文件写坏，
     所以本补丁全程用**字节替换**，绝不改动文件其余部分的编码。

幂等：只在需要时改，重复运行无副作用。
"""

import ctypes
import pathlib
import re


def _targets():
    import pykinect2
    base = pathlib.Path(pykinect2.__file__).parent
    return [base / "PyKinectV2.py", base / "PyKinectRuntime.py"]


def ensure_patched(verbose=True):
    """保证 pykinect2 能在当前平台导入并运行。返回是否做了修改。"""
    changed_any = False

    for path in _targets():
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        orig = raw

        # ---- 1) tagSTATSTG 结构大小（32 位假设 vs 64 位实测） ----
        if b"sizeof(tagSTATSTG) == 72" in raw:
            try:
                import comtypes.automation as ca
                real = ctypes.sizeof(ca.tagSTATSTG)
            except Exception:
                real = 80
            raw = raw.replace(b"sizeof(tagSTATSTG) == 72",
                              f"sizeof(tagSTATSTG) == {real}".encode("ascii"))
            if verbose:
                print(f"[patch] tagSTATSTG 断言 72 -> {real}（64 位实测值）")

        # ---- 2) comtypes._check_version 旧签名 ----
        if b"_check_version" in raw:
            raw = re.sub(
                rb"^[ \t]*from comtypes import _check_version[ \t]*;?[ \t]*_check_version\([^)]*\)[ \t]*\r?\n",
                b"# _check_version removed by pykinect2_compat.py (new comtypes has no such API)\n",
                raw, flags=re.M)
            if verbose:
                print("[patch] 移除 comtypes _check_version 校验")

        # ---- 3) time.clock() 在 Python 3.8+ 已被移除 ----
        if b"time.clock()" in raw:
            raw = raw.replace(b"time.clock()", b"time.perf_counter()")
            if verbose:
                print(f"[patch] time.clock() -> time.perf_counter()（{path.name}）")

        if raw == orig:
            continue
        try:
            path.write_bytes(raw)
            changed_any = True
        except OSError as e:
            if verbose:
                print(f"[patch] 写回失败: {e}")

    return changed_any

    # ---- 1) tagSTATSTG 结构大小 ----
    if b"sizeof(tagSTATSTG) == 72" in raw:
        try:
            import comtypes.automation as ca
            real = ctypes.sizeof(ca.tagSTATSTG)
        except Exception:
            real = 80
        raw = raw.replace(b"sizeof(tagSTATSTG) == 72",
                          f"sizeof(tagSTATSTG) == {real}".encode("ascii"))
        if verbose:
            print(f"[patch] tagSTATSTG 断言 72 -> {real}（64 位实测值）")

    # ---- 2) comtypes._check_version 旧签名 ----
    if b"_check_version" in raw:
        raw = re.sub(
            rb"^[ \t]*from comtypes import _check_version[ \t]*;?[ \t]*_check_version\([^)]*\)[ \t]*\r?\n",
            b"# _check_version removed by pykinect2_compat.py (new comtypes has no such API)\n",
            raw, flags=re.M)
        if verbose:
            print("[patch] 移除 comtypes _check_version 校验")

if __name__ == "__main__":
    print("已修改" if ensure_patched() else "无需修改")
