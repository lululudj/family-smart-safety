#!/usr/bin/env python3
"""
塔台 MCP Server —— 家庭空地异构机器人系统的能力出口

当前为骨架版本（路线 A）：暴露少量"假工具"，用于验证
小智 ESP32 → 小智云 → 本地 proxy → 本 Server 的链路是否通畅。

后续（路线 B）在此文件中替换/追加真实工具即可，例如：
  - query_scene     查询 Kinect 感知结果
  - dispatch_uav    派无人机去某房间
  - get_map         查询家庭地图坐标
  - check_stove     燃气灶状态识别

运行：
    python3 tower_server.py            # stdio 模式（供 mcp_pipe 调用）
    python3 tower_server.py --http     # streamable-http 模式（供 http proxy 调用）
"""

import asyncio
import platform
import socket
import sys
from datetime import datetime

try:
    # mcp 2.x: FastMCP 已更名为 MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
    _API = "2.x"
except ImportError:
    try:
        # mcp 1.x 兼容路径
        from mcp.server.fastmcp import FastMCP as _Server
        _API = "1.x"
    except ImportError:
        print("缺少 mcp 库，请先安装：pip install mcp", file=sys.stderr)
        sys.exit(1)

# ---------------------------------------------------------------- 塔台状态模拟

# 真实实现时，这里应改为读取 Kinect / 无人机 / 机器狗的实际状态
_TOWER_STATE = {
    "tower_online": True,
    "kinect_connected": False,   # 等 Kinect 到货验机后置 True
    "uav_count": 0,
    "ugv_count": 0,
    "last_update": None,
}


def _touch():
    _TOWER_STATE["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- MCP Server

mcp = _Server("home-tower")
# 1.x 的 tool 是需调用的方法；2.x 的 tool 是装饰器工厂，也必须 @tool() 调用。
# 两者用法一致，统一封一层即可。
_TOOL_FN = mcp.tool()


@_TOOL_FN
def ping() -> str:
    """连通性测试。用于确认塔台 MCP Server 是否在线。"""
    _touch()
    return "pong —— 塔台 MCP Server 正常响应"


@_TOOL_FN
def tower_status() -> dict:
    """查询塔台整体状态，包括 Kinect 是否接入、已注册的无人机与机器狗数量。"""
    _touch()
    return {
        "塔台在线": _TOWER_STATE["tower_online"],
        "Kinect已连接": _TOWER_STATE["kinect_connected"],
        "无人机数量": _TOWER_STATE["uav_count"],
        "机器狗数量": _TOWER_STATE["ugv_count"],
        "最后更新": _TOWER_STATE["last_update"],
    }


@_TOOL_FN
def tower_hardware() -> dict:
    """查询塔台宿主机的硬件与网络信息（CPU 架构、主机名、IP）。"""
    hostname = socket.gethostname()
    try:
        ip = socket.gethostbyname(hostname)
    except Exception:
        ip = "unknown"
    return {
        "主机名": hostname,
        "IP地址": ip,
        "系统": platform.platform(),
        "架构": platform.machine(),
        "Python": platform.python_version(),
    }


@_TOOL_FN
def list_capabilities() -> list:
    """列出塔台当前具备的能力清单，以及尚未就绪的能力。"""
    return [
        {"能力": "Kinect外部视觉定位", "状态": "待硬件到货", "阶段": "P0"},
        {"能力": "无人机自主巡逻", "状态": "未开始", "阶段": "P1"},
        {"能力": "异常识别与家属推送", "状态": "未开始", "阶段": "P2"},
        {"能力": "空地任务派发", "状态": "未开始", "阶段": "P3"},
        {"能力": "机器狗物理干预", "状态": "长期，未开始", "阶段": "P4"},
    ]


# ---------------------------------------------------------------- 入口

if __name__ == "__main__":
    print(f"[tower] mcp API 版本: {_API}", file=sys.stderr)
    if "--http" in sys.argv:
        # HTTP 模式，供 REST/HTTP 型 proxy 连接
        if _API == "1.x":
            mcp.run(transport="streamable-http", host="127.0.0.1", port=12306)
        else:
            mcp.run(transport="streamable-http", host="127.0.0.1", port=12306)
    else:
        # stdio 模式，供 mcp_pipe.py 作为子进程拉起
        if _API == "1.x":
            asyncio.run(mcp.run_stdio_async())
        else:
            mcp.run(transport="stdio")
