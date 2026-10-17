import asyncio, os, edge_tts

VOICE = "zh-CN-YunxiNeural"
OUT = r"C:\Users\Administrator\WorkBuddy\2026-10-07-17-39-43\remotion-videos\public\assets\narration"

LINES = {
    "n1": "出门在外，家里的火，你还记得关没关吗？",
    "n2": "塔台派出空中之眼，起飞巡逻，全屋不留死角。",
    "n3": "三维视觉中枢，实时掌握全屋态势，毫秒级更新。",
    "n4": "厨房，蓝色火焰，异常被立刻锁定，回传塔台。",
    "n5": "塔台派单，地面机器狗携机械臂，直奔现场。",
    "n6": "机械爪稳稳转动旋钮，燃气阀门，关闭。",
    "n7": "现场画面，第一时间送到家人手机里，安心。",
    "n8": "眼、脑、手，三位一体。",
}


async def main():
    os.makedirs(OUT, exist_ok=True)
    for name, text in LINES.items():
        path = os.path.join(OUT, name + ".mp3")
        comm = edge_tts.Communicate(text, VOICE)
        await comm.save(path)
        size = os.path.getsize(path)
        print(f"{name}.mp3  {size} bytes  <- {text}")


asyncio.run(main())
