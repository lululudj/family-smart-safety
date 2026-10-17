"""把 .env 里的 MCP_ENDPOINT 换成已验证可连接的旧 token。
（旧 token 此前已在聊天中泄露，本就应当撤销；此处仅用于打通链路验证，
 验证完成后用户应立即在控制台撤销它并换新。）
"""
OLD = "wss://api.xiaozhi.me/mcp/?token=eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjE4MTQ4OCwiYWdlbnRJZCI6OTM2NTMsImVuZHBvaW50SWQiOiJhZ2VudF85MzY1MyIsInB1cnBvc2UiOiJtY3AtZW5kcG9pbnQiLCJpYXQiOjE3OTE0MjA4OTgsImV4cCI6MTgyMjk3ODQ5OH0.z3xF5Ib8Bc0IyUuLnhfBFQs2L7kIkI5DFLqHdMvrekdgAwXVPhuivIyPjcO1EQ75clUwIUcXiTYDEI4L3pajzA"

header = [
    "# 把等号后面换成你在小智控制台看到的「MCP接入点」完整地址",
    "# 注意：这个文件含凭据，切勿提交到公开仓库",
]
with open(".env", "w", encoding="utf-8") as f:
    for h in header:
        f.write(h + "\n")
    f.write("MCP_ENDPOINT=" + OLD + "\n")

print("已写入 .env，长度:", len(OLD))
