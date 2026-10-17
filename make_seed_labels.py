# -*- coding: utf-8 -*-
"""自举第1步：回滚到原始标签，仅保留验证过的干净种子。
- 删除 12 个毒标签（手机/遮挡/杂物）
- 写入 8 个已验证的提议框
- 其余原始自动标签作为种子保留（含部分偏松/偏紧但中心正确的）
- 我此前手写的 115 个框全部回滚（不可靠）
"""
import os, shutil, json

ROOT = r"e:\全家智能安全调度系统"
DS = os.path.join(ROOT, "datasets", "tello_kinect")
OUT = os.path.join(DS, "relabel_20261017")
BAK = os.path.join(OUT, "labels_backup")
W, H = 960, 540

DELETE = ["130120_005", "130120_957", "130121_773", "130123_404", "130124_218",
          "130125_032", "130127_715", "130230_299", "130231_109", "130232_727",
          "130122_588"]
# 已验证的提议框（negprop 复核通过）
VERIFIED_PROP = {
    "130135_946": (415, 160, 500, 215), "130136_755": (415, 158, 500, 213),
    "130226_796": (364, 120, 500, 212), "130518_285": (257, 120, 368, 194),
    "130522_342": (307, 118, 418, 191), "130556_788": (261, 118, 378, 198),
    "130600_030": (263, 133, 424, 206), "130622_710": (305, 127, 428, 200),
}

def find(short):
    for split in ("train", "val"):
        p = os.path.join(DS, "images", split, "20261017_" + short + ".jpg")
        if os.path.isfile(p):
            return split
    return None

# 1) 恢复备份
n = 0
for split in ("train", "val"):
    src = os.path.join(BAK, split)
    dst = os.path.join(DS, "labels", split)
    for f in os.listdir(src):
        if f.endswith(".txt"):
            shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
            n += 1
print("restored", n, "label files")

# 2) 删除毒标签
for short in DELETE:
    split = find(short)
    if split:
        open(os.path.join(DS, "labels", split, "20261017_" + short + ".txt"), "w").close()
print("deleted", len(DELETE), "poison labels")

# 3) 写入已验证提议框
def to_yolo(x1, y1, x2, y2):
    return f"0 {(x1+x2)/2/W:.6f} {(y1+y2)/2/H:.6f} {(x2-x1)/W:.6f} {(y2-y1)/H:.6f}"

for short, box in VERIFIED_PROP.items():
    split = find(short)
    if split:
        with open(os.path.join(DS, "labels", split, "20261017_" + short + ".txt"), "w") as f:
            f.write(to_yolo(*box) + "\n")
print("wrote", len(VERIFIED_PROP), "verified proposals")

pos = neg = 0
for split in ("train", "val"):
    ld = os.path.join(DS, "labels", split)
    for f in os.listdir(ld):
        if f.endswith(".txt"):
            if os.path.getsize(os.path.join(ld, f)) > 0:
                pos += 1
            else:
                neg += 1
print(f"seeds: pos={pos} neg={neg}")
