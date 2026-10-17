#!/usr/bin/env python3
"""ai_butler —— 家庭 AI 管家（笔记本侧大脑）

架构：
    大脑  DeepSeek API（有网）/ Ollama 本地小模型（没网自动切换）
    眼睛  Kinect + YOLO（塔台 /api/status 里的 vision 数据，含三维坐标）
    手脚  塔台 API（无人机起飞/降落/急停、巡逻任务、Kinect 扫描）
    神经  HTTP → 香橙塔台(192.168.18.181:8080) → 设备

安全设计：
    - 所有设备动作走塔台白名单 API，塔台侧有安全锁（电量/连接检查）
    - AI 没有绕过塔台的通道，每个动作都经过 /api/* 留痕

用法：
    python ai_butler.py                  # 交互对话
    python ai_butler.py --once "看看家里有什么"   # 单问单答（测试用）
"""
import argparse
import json
import os
import sys
import urllib.request

import requests

# ---------------------------------------------------------------- 配置
ROOT = os.path.dirname(os.path.abspath(__file__))


def load_env():
    """读项目根目录 .env（KEY=VALUE 格式）

    .env 优先于系统环境变量：本项目配置的 key 应覆盖系统里可能残留的旧值。
    """
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()


load_env()

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
TOWER = os.environ.get("TOWER", "http://127.0.0.1:8080")  # 塔台已迁到本机

SYSTEM_PROMPT = """你是"家庭管家"，一个负责家庭安全与设备调度 AI 助手。

你通过工具了解家里情况、操控设备：
- 眼睛：Kinect 摄像头 + YOLO 识别（可看到物体及三维坐标，x 右 y 下 z 前，单位米）
- 手脚：Tello 无人机（可起飞/降落/急停）、巡逻任务、卧室扫描

回答原则：
1. 用户问"家里有什么/看到什么"时，调用 look_around 工具拿真实数据，基于数据回答，不要编造
2. 报告物体位置时用"前方 X 米、偏左/右 Y 米"这类人话，不要输出裸坐标
3. 涉及起飞等危险动作时先口头确认安全要点（桨叶、周围无人），再执行
4. 设备不在线/失败时如实说明，不要假装成功
5. 用简洁中文回答
"""

# ---------------------------------------------------------------- 工具实现（全走塔台 API）

def tool_look_around():
    r = requests.get(f"{TOWER}/api/status", timeout=8)
    d = r.json()
    vision = d.get("vision") or {}
    objs = vision.get("objects") or []
    change = vision.get("change") or {}
    people = [o for o in objs if o.get("label") == "person"]
    frame = d.get("kinect_frame") or {}
    return {
        "vision_active": vision.get("active", False),
        "camera_live": frame.get("available", False),
        "camera_age_s": frame.get("age_s"),
        "change_level": change.get("level"),
        "change_ratio": change.get("ratio"),
        "objects": [
            {"label": o.get("label"), "conf": o.get("conf"),
             "x_m": o.get("x"), "y_m": o.get("y"), "z_m": o.get("z")}
            for o in objs
        ],
        "person_count": len(people),
        "note": "x右正 y下正 z前正，单位米；z 为 null 表示该物体没有测到深度" if objs else None,
    }


def tool_system_status():
    r = requests.get(f"{TOWER}/api/status", timeout=8)
    d = r.json()
    tower = d.get("tower") or {}
    uav = d.get("uav") or {}
    video = d.get("uav_video") or {}
    return {
        "tower_online": tower.get("online"),
        "tower_ip": tower.get("ip"),
        "cpu_temp": tower.get("cpu_temp"),
        "memory_percent": (tower.get("memory") or {}).get("percent"),
        "uav_online": uav.get("online"),
        "uav_battery": uav.get("battery"),
        "uav_video_running": video.get("running"),
        "video_error": video.get("error"),
        "ugv": d.get("ugv"),
        "kinect_frame": d.get("kinect_frame"),
    }


def tool_room_info():
    return {
        "room": "卧室",
        "size_m": "4.61 × 2.89",
        "area_m2": 13.3,
        "height_m": 3.58,
        "model_version": "room_model_v5.glb（RTAB-Map 扫描 + AI 超分贴图）",
        "web_view": f"{TOWER}/room3d.html",
    }


def tool_send_mission(mission_type: str, target: str):
    r = requests.post(f"{TOWER}/api/mission",
                      json={"type": mission_type, "target": target}, timeout=8)
    return r.json()


def tool_uav_command(cmd: str):
    allowed = {"takeoff", "land", "emergency"}
    if cmd not in allowed:
        return {"ok": False, "msg": f"不允许的命令 {cmd}，只允许 {sorted(allowed)}"}
    r = requests.post(f"{TOWER}/api/uav/cmd", json={"cmd": cmd}, timeout=15)
    return r.json()


TOOLS_IMPL = {
    "look_around": tool_look_around,
    "get_system_status": tool_system_status,
    "get_room_info": tool_room_info,
    "send_mission": lambda mission_type, target: tool_send_mission(mission_type, target),
    "uav_command": lambda cmd: tool_uav_command(cmd),
}

TOOLS_SPEC = [
    {"type": "function", "function": {
        "name": "look_around",
        "description": "看一眼家里现在的实时情况：YOLO 识别到的物体、人物数量、物体的三维位置、活动变化度",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "get_system_status",
        "description": "查询塔台与设备状态：塔台是否在线、无人机连接与电量、视频链路、CPU/内存等",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "get_room_info",
        "description": "查询已建模房间的信息（尺寸、面积、层高、模型版本）",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "send_mission",
        "description": "向塔台派发任务（记录到任务中心）",
        "parameters": {"type": "object",
                       "properties": {"mission_type": {"type": "string", "enum": ["ping", "patrol", "dispatch"]},
                                      "target": {"type": "string", "description": "目标区域，如：全屋、卧室"}},
                       "required": ["mission_type", "target"]}}},
    {"type": "function", "function": {
        "name": "uav_command",
        "description": "向无人机发送飞行动作指令。危险操作：takeoff 起飞前必须先口头提醒用户确认桨叶与人员安全；emergency 是电机急停（空中使用会坠落）",
        "parameters": {"type": "object",
                       "properties": {"cmd": {"type": "string", "enum": ["takeoff", "land", "emergency"]}},
                       "required": ["cmd"]}}},
]

# ---------------------------------------------------------------- 大脑（在线 DeepSeek / 离线 Ollama）

def chat(messages, base_url, api_key, model):
    body = {"model": model, "messages": messages, "tools": TOOLS_SPEC, "temperature": 0.3}
    req = urllib.request.Request(
        base_url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def brain_available(base_url, api_key):
    """探测大脑是否可达（2 秒超时）"""
    try:
        body = {"model": "deepseek-chat" if "deepseek" in base_url else OLLAMA_MODEL,
                "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
        req = urllib.request.Request(base_url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {api_key}"})
        urllib.request.urlopen(req, timeout=4)
        return True
    except Exception:
        return False


def ask(messages):
    """一轮问答：自动选择大脑，处理工具调用循环（最多 5 轮）"""
    online = DEEPSEEK_KEY and brain_available(DEEPSEEK_URL, DEEPSEEK_KEY)
    if online:
        base, key, model, tag = DEEPSEEK_URL, DEEPSEEK_KEY, DEEPSEEK_MODEL, f"DeepSeek({DEEPSEEK_MODEL})"
    else:
        base, key, model, tag = OLLAMA_URL, "ollama", OLLAMA_MODEL, f"Ollama({OLLAMA_MODEL})"
        if not brain_available(base, key):
            return "⚠️ 大脑不可用：DeepSeek API 连不上（没网？），本地 Ollama 也没启动。请检查网络或运行 `ollama serve`。"

    print(f"[大脑: {tag}]")
    work = list(messages)
    for _ in range(5):
        data = chat(work, base, key, model)
        msg = data["choices"][0]["message"]
        calls = msg.get("tool_calls")
        if not calls:
            return msg.get("content", "（空回复）")
        work.append(msg)
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = TOOLS_IMPL.get(name)
            try:
                result = fn(**args) if fn else {"error": f"未知工具 {name}"}
            except Exception as e:
                result = {"error": str(e)}
            print(f"  🔧 {name}({json.dumps(args, ensure_ascii=False)})")
            work.append({"role": "tool", "tool_call_id": call["id"],
                         "content": json.dumps(result, ensure_ascii=False)})
    return "（工具调用轮次用尽，问题太复杂了）"


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", metavar="QUESTION", help="单问单答后退出")
    args = ap.parse_args()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if args.once:
        messages.append({"role": "user", "content": args.once})
        print(f"你: {args.once}")
        print(f"管家: {ask(messages)}")
        return

    print("家庭 AI 管家已启动（Ctrl+C 退出）。试试：看看家里有什么 / 无人机什么状态 / 帮我全屋巡逻")
    while True:
        try:
            q = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            return
        if not q:
            continue
        if q.lower() in ("exit", "quit", "退出"):
            print("再见")
            return
        messages.append({"role": "user", "content": q})
        print(f"管家: {ask(messages)}")
        messages = messages[-20:]   # 控制上下文长度


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止")
        sys.exit(0)
