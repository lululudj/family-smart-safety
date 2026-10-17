# -*- coding: utf-8 -*-
"""为需要修框/补框的帧生成 40px 坐标网格放大图（时间就近匹配文件名），每 sheet 4 格"""
import os, json, re
import numpy as np
import cv2

ROOT = r"e:\全家智能安全调度系统"
DS = os.path.join(ROOT, "datasets", "tello_kinect")
OUT = os.path.join(DS, "relabel_20261017")

def imread_cn(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)

def imwrite_cn(p, img, q=90):
    cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tofile(p)

def yolo_xyxy(l, W, H):
    _, xc, yc, w, h = map(float, l.split())
    return int((xc - w / 2) * W), int((yc - h / 2) * H), int((xc + w / 2) * W), int((yc + h / 2) * H)

def tsec(short):
    m = re.match(r"(\d\d)(\d\d)(\d\d)_(\d+)", short)
    if not m:
        return None
    hh, mm, ss, ms = map(int, m.groups())
    return hh * 3600 + mm * 60 + ss + ms / 1000.0

# 实际文件索引
ALL_FILES = {}
for split in ("train", "val"):
    d = os.path.join(DS, "images", split)
    for f in os.listdir(d):
        if f.endswith(".jpg"):
            short = f[:-4].replace("20261017_", "")
            ALL_FILES[short] = (split, os.path.join(d, f))

def resolve(short):
    """精确匹配；失败则取时间最近的文件（±3s 内）"""
    if short in ALL_FILES:
        return short
    t = tsec(short)
    if t is None:
        return None
    best, bd = None, 3.0
    for s2 in ALL_FILES:
        t2 = tsec(s2)
        if t2 is not None and abs(t2 - t) < bd:
            best, bd = s2, abs(t2 - t)
    return best

def grid_frame(img, box):
    H, W = img.shape[:2]
    for x in range(0, W, 40):
        cv2.line(img, (x, 0), (x, H), (70, 70, 70), 1)
    for y in range(0, H, 40):
        cv2.line(img, (0, y), (W, y), (70, 70, 70), 1)
    for x in range(0, W, 200):
        cv2.putText(img, str(x), (x + 2, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 200, 255), 1)
    for y in range(40, H, 200):
        cv2.putText(img, str(y), (2, y + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 200, 255), 1)
    if box is not None:
        cv2.rectangle(img, (box[0], box[1]), (box[2], box[3]), (0, 0, 255), 2)
    return img

props = json.load(open(os.path.join(OUT, "proposals.json"), encoding="utf-8"))
key_by_short = {}
for k in props:
    key_by_short[k.split("/")[1].replace("20261017_", "")] = k

FIX_POS = sorted(set(["130132_659", "130133_516", "130134_326", "130137_566", "130138_374",
                      "130225_985", "130231_918", "130234_385", "130236_006", "130243_642",
                      "130247_697", "130510_892", "130513_323", "130524_773", "130528_159",
                      "130532_213", "130541_138", "130541_948", "130548_445", "130553_309",
                      "130554_121", "130555_977", "130621_087", "130228_680", "130506_034",
                      "130510_082", "130620_278", "130131_037", "130135_135", "130139_185"]))
NEW_NEG = sorted(set(["130129_336", "130227_606", "130233_575", "130235_295", "130241_930",
                      "130242_441", "130244_452", "130245_263", "130246_075", "130246_886",
                      "130444_055", "130444_865", "130445_673", "130446_484", "130447_294",
                      "130448_106", "130448_912", "130450_003", "130450_812", "130451_623",
                      "130452_430", "130453_245", "130454_054", "130456_254", "130457_864",
                      "130457_876", "130458_686", "130459_776", "130500_304", "130501_117",
                      "130501_227", "130502_742", "130503_552", "130504_362", "130505_272",
                      "130506_644", "130507_652", "130511_704", "130512_513", "130514_134",
                      "130514_992", "130515_856", "130516_666", "130517_475", "130519_096",
                      "130519_907", "130520_717", "130521_632", "130523_131", "130523_962",
                      "130525_683", "130526_607", "130527_318", "130528_969", "130529_778",
                      "130531_877", "130533_026", "130533_827", "130534_646", "130535_446",
                      "130536_276", "130537_085", "130538_707", "130542_767", "130546_818",
                      "130550_876", "130554_931", "130603_271", "130607_220", "130611_369",
                      "130613_800", "130615_926", "130623_521", "130624_532", "130626_106",
                      "130626_917", "130627_728", "130628_537", "130131_848", "130126_908",
                      "130224_439", "130236_617"]))

def make_sheets(items, prefix, draw_prop=False):
    cells, resolved = [], []
    for short in items:
        r = resolve(short)
        if r is None:
            print("unresolved", short)
            continue
        split, p = ALL_FILES[r]
        img = imread_cn(p)
        H, W = img.shape[:2]
        box = None
        lbl = os.path.join(DS, "labels", split, "20261017_" + r + ".txt")
        if os.path.isfile(lbl) and os.path.getsize(lbl) > 0:
            for line in open(lbl, encoding="utf-8"):
                if line.strip():
                    box = yolo_xyxy(line, W, H)
        if draw_prop and r in key_by_short:
            pb = props[key_by_short[r]]["boxes"]
            if pb:
                box = tuple(map(int, pb[0]["box"]))  # 用提议框作底
        img = grid_frame(img, box)
        canvas = np.zeros((428, 760, 3), np.uint8)
        canvas[:, :] = cv2.resize(img, (760, 428))
        cv2.putText(canvas, r, (4, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cells.append(canvas)
        resolved.append(r)
    n = 0
    for k in range(0, len(cells), 4):
        b = cells[k:k + 4]
        while len(b) % 2:
            b.append(np.zeros((428, 760, 3), np.uint8))
        rows = [np.hstack(b[i:i + 2]) for i in range(0, len(b), 2)]
        n += 1
        imwrite_cn(os.path.join(OUT, f"{prefix}_{n:02d}.jpg"), np.vstack(rows), 88)
    return resolved, n

r1, s1 = make_sheets(FIX_POS, "fixpos")
r2, s2 = make_sheets(NEW_NEG, "fixneg")
# NEG-with-proposal 12 张：画提议框核对
neg_prop_shorts = sorted(k.split("/")[1].replace("20261017_", "")
                         for k in props if not props[k]["has_lbl"] and props[k]["boxes"])
r3, s3 = make_sheets(neg_prop_shorts, "negprop", draw_prop=True)
with open(os.path.join(OUT, "resolved_lists.json"), "w", encoding="utf-8") as f:
    json.dump({"fixpos": r1, "fixneg": r2, "negprop": r3}, f, ensure_ascii=False, indent=1)
print(f"fixpos {len(r1)} -> {s1} sheets; fixneg {len(r2)} -> {s2} sheets; negprop {len(r3)} -> {s3} sheets")
