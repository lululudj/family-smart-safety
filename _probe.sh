#!/usr/bin/env bash
# 手工验证 tower_server.py 的 stdio MCP 握手是否正常
# 模拟 mcp_pipe.py 的行为：通过 stdin 发 JSON-RPC，读 stdout 响应
cd /home/orangepi/tower_mcp || exit 1

echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1.0"}}}' > /tmp/req.txt
echo '{"jsonrpc":"2.0","method":"notifications/initialized"}' >> /tmp/req.txt
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' >> /tmp/req.txt

timeout 12 .venv/bin/python tower_server.py < /tmp/req.txt 2>/tmp/tower_err.txt
echo "=== exit: $? ==="
echo "=== stderr ==="
cat /tmp/tower_err.txt
