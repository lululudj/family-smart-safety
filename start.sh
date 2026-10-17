#!/usr/bin/env bash
# 小智 ⇄ 塔台 MCP Server 桥接启动脚本
#
# 用法：
#   1. 首次运行前，把你的 MCP 接入点地址填到本目录下的 .env 文件里
#      （不要提交到任何公开仓库）
#   2. ./start.sh
#
# 原理：
#   mcp_pipe.py 主动以 WebSocket 连到小智云（用 MCP_ENDPOINT），
#   同时把 tower_server.py 作为子进程用 stdio 拉起，
#   两端双向转发 JSON-RPC。
#   所以连上后控制台里"接入点状态"会变成已连接。

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ ! -f .env ]; then
    echo "错误：缺少 .env 文件"
    echo "请先创建 .env，内容如下（换成你自己的地址）："
    echo "  MCP_ENDPOINT=wss://api.xiaozhi.me/mcp/?token=你的token"
    exit 1
fi

# shellcheck disable=SC1091
set -a
. ./.env
set +a

if [ -z "${MCP_ENDPOINT:-}" ]; then
    echo "错误：.env 里没有设置 MCP_ENDPOINT"
    exit 1
fi

PY="$DIR/.venv/bin/python"

# 关键：mcp_pipe.py 内部用裸 "python" 拉起子进程，
# 必须让 venv 的 python 排在 PATH 最前，否则会找不到命令或用错解释器
export PATH="$DIR/.venv/bin:$PATH"

echo "[start] 使用解释器: $PY"
echo "[start] MCP 接入点已配置（长度 ${#MCP_ENDPOINT}）"
echo "[start] 正在连接小智云并拉起塔台 Server..."
echo "        按 Ctrl+C 停止"
echo

exec "$PY" mcp_pipe.py tower_server.py
