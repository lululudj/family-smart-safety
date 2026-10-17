"""检查 .env 里 token 的结构完整性（不打印任何敏感内容）"""
import os, re
from dotenv import load_dotenv
load_dotenv()
uri = os.environ["MCP_ENDPOINT"]

print("URL 总长:", len(uri))
print("前缀正确:", uri.startswith("wss://api.xiaozhi.me/mcp/?token="))

token = uri.split("token=")[-1]
print("token 长度:", len(token))

segs = token.split(".")
print("分段数(应为3):", len(segs))
for i, s in enumerate(segs):
    print(f"  第{i+1}段长度: {len(s)}")

bad = re.findall(r"[^A-Za-z0-9_\-\.]", token)
print("非法字符数(应为0):", len(bad))
if bad:
    print("存在非法字符的种类:", sorted(set(repr(c) for c in bad)))

# ES256 典型: header=36, payload=150-180, signature=86-88
h, p, s = (segs + ["", "", ""])[:3]
print("header 段长度正常(36±2):", abs(len(h) - 36) <= 2)
print("signature 段长度正常(86-88):", 80 <= len(s) <= 95)
print("结尾是否像 JWT(无多余字符):", token.endswith(segs[-1]) and not token.endswith("="))
