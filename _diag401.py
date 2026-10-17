"""诊断小智 MCP 接入点 401：打印服务器返回的完整 HTTP 状态、头、体"""
import asyncio, os, sys
from dotenv import load_dotenv
load_dotenv()
uri = os.environ["MCP_ENDPOINT"]

async def main():
    import websockets
    print("websockets 版本:", websockets.__version__)
    try:
        async with websockets.connect(uri) as ws:
            print("连接成功!")
            print("子协议:", ws.subprotocol)
    except Exception as e:
        print("异常类型:", type(e).__name__)
        print("异常信息:", str(e)[:500])
        # websockets>=12 的 InvalidStatus 带 response 对象
        r = getattr(e, "response", None)
        if r is not None:
            print("HTTP 状态:", getattr(r, "status_code", "?"))
            try:
                body = r.body if isinstance(r.body, str) else r.body.decode("utf-8", "replace")
                print("响应体:", body[:1000])
            except Exception:
                pass
            try:
                for k, v in r.headers.items():
                    print(f"  响应头 {k}: {v}")
            except Exception:
                pass

asyncio.run(main())
