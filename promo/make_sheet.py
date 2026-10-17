import subprocess, os
from PIL import Image

FFMPEG = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\npx.cmd"
PROJ = r"C:\Users\Administrator\WorkBuddy\2026-10-07-17-39-43\remotion-videos"
TMP = os.path.join(PROJ, "public", "_tmpframes")
OUT = os.path.join(PROJ, "public")
os.makedirs(TMP, exist_ok=True)

TIMES = [0.2, 1.0, 1.8, 2.6, 3.4, 4.2]


def extract(shot):
    paths = []
    for i, t in enumerate(TIMES):
        p = os.path.join(TMP, f"{shot}_{i}.jpg")
        cmd = [
            FFMPEG, "remotion", "ffmpeg", "-y",
            "-ss", str(t),
            "-i", os.path.join(PROJ, "public", "assets", f"{shot}.mp4"),
            "-vframes", "1", "-q:v", "3", p,
        ]
        subprocess.run(cmd, cwd=PROJ, capture_output=True)
        if os.path.exists(p):
            paths.append(p)
    return paths


def montage(paths, out):
    if not paths:
        print("no frames extracted")
        return
    imgs = [Image.open(p).resize((420, int(420 * Image.open(p).height / Image.open(p).width))) for p in paths]
    w = 420 * 3
    h = imgs[0].height * 2
    sheet = Image.new("RGB", (w, h), (20, 20, 20))
    for idx, im in enumerate(imgs):
        x = (idx % 3) * 420
        y = (idx // 3) * imgs[0].height
        sheet.paste(im, (x, y))
    sheet.save(out)
    print("saved", out, sheet.size)


for shot in ["shot1", "shot2", "shot3"]:
    montage(extract(shot), os.path.join(OUT, f"sheet_{shot}.png"))
