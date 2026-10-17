"""对照实验：旧 token（聊天里泄露那个）能否连上？
- 旧 token 能连  -> 用户复制新 token 的环节有问题（粘贴损坏/复制到一半）
- 旧 token 也 401 -> 不是 token 本身的问题（账号/API 层面变化）
不打印任何 token 内容。
"""
import asyncio

OLD = "wss://api.xiaozhi.me/mcp/?token=eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjE4MTQ4OCwiYWdlbnRJZCI6OTM2NTMsImVuZHBvaW50SWQiOiJhZ2VudF85MzY1MyIsInB1cnBvc2UiOiJtY3AtZW5kcG9pbnQiLCJpYXQiOjE3OTE0MjA4OTgsImV4cCI6MTgyMjk3ODQ5OH0.z3xF5Ib8Bc0IyUuLnhfBFQs2L7kIkI5DFLqHdMvrekdgAwXVPhuivIyPjcO1EQ75clUwIUcXiTYDEI4L3pajzA"

async def main():
    import websockets
    try:
        async with websockets.connect(OLD, open_timeout=15) as ws:
            print("旧 token: 连接成功!")
            print("子协议:", ws.subprotocol)
    except Exception as e:
        r = getattr(e, "response", None)
        status = getattr(r, "status_code", "?")
        print("旧 token: 失败 ->", type(e).__name__, status)
        if r is not None and r.body:
            b = r.body if isinstance(r.body, str) else r.body.decode("utf-8","replace")
            print("响应体:", b[:200])

asyncio.run(main())
