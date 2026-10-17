import os, shutil

SRC = r"C:\Users\Administrator\WorkBuddy\2026-10-07-17-39-43\promo\clips2"
DST = r"C:\Users\Administrator\WorkBuddy\2026-10-07-17-39-43\remotion-videos\public\assets"

# shot order -> identifying substring in the generated filename
MAPPING = [
    ("e1", "一位头发花"),
    ("e2", "家用监控摄像头特写"),
    ("e3", "书房桌面"),
    ("e4", "客厅地面视角"),
    ("e5", "老人坐在地板上"),
    ("e6", "机器狗的头部"),
    ("e7", "现代办公室"),
    ("e8", "住宅门口"),
]

files = os.listdir(SRC)
for name, key in MAPPING:
    hit = [f for f in files if key in f]
    if not hit:
        print(f"MISS {name} key={key}")
        continue
    src = os.path.join(SRC, hit[0])
    dst = os.path.join(DST, name + ".mp4")
    shutil.copy(src, dst)
    print(f"{name}.mp4  {os.path.getsize(dst)} bytes  <- {hit[0][:36]}")
