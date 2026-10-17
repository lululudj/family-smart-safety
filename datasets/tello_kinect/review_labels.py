# -*- coding: utf-8 -*-
"""人工抽查自动标注质量：把 YOLO 框画到图上，生成 HTML 审查页（正样本全量 + 负样本随机抽样）"""
import os, random, html
import numpy as np
import cv2

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "review_20261017")
os.makedirs(OUT, exist_ok=True)

def imread_cn(path):
    """cv2.imread 不支持中文路径，用 fromfile+imdecode 代替"""
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)

def imwrite_cn(path, img, params=None):
    ok, buf = cv2.imencode(os.path.splitext(path)[1], img, params or [])
    if ok:
        buf.tofile(path)
    return ok

def yolo_to_xyxy(xc, yc, w, h, W, H):
    x1 = int((xc - w / 2) * W); y1 = int((yc - h / 2) * H)
    x2 = int((xc + w / 2) * W); y2 = int((yc + h / 2) * H)
    return max(0, x1), max(0, y1), min(W - 1, x2), min(H - 1, y2)

def render(split, name, thumb_w=640):
    """返回 (缩略图文件名, 标注框像素宽, 标注框像素高)；无框时框尺寸为 None"""
    img_path = os.path.join(ROOT, "images", split, name + ".jpg")
    lbl_path = os.path.join(ROOT, "labels", split, name + ".txt")
    img = imread_cn(img_path)
    if img is None:
        return None, None, None
    H, W = img.shape[:2]
    boxes = []
    if os.path.isfile(lbl_path):
        for line in open(lbl_path, encoding="utf-8"):
            p = line.split()
            if len(p) == 5:
                x1, y1, x2, y2 = yolo_to_xyxy(*map(float, p[1:]), W, H)
                boxes.append((x1, y1, x2, y2))
    for i, (x1, y1, x2, y2) in enumerate(boxes):
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(img, f"#{i}", (x1, max(14, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    scale = thumb_w / W
    thumb = cv2.resize(img, (thumb_w, int(H * scale)))
    out_name = f"{split}_{name}.jpg"
    imwrite_cn(os.path.join(OUT, out_name), thumb, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if boxes:
        bw = int((boxes[0][2] - boxes[0][0]))
        bh = int((boxes[0][3] - boxes[0][1]))
        return out_name, bw, bh
    return out_name, None, None

def collect(split):
    pos, neg = [], []
    for f in sorted(os.listdir(os.path.join(ROOT, "images", split))):
        if not f.endswith(".jpg"):
            continue
        name = f[:-4]
        lbl = os.path.join(ROOT, "labels", split, name + ".txt")
        (pos if os.path.isfile(lbl) and os.path.getsize(lbl) > 0 else neg).append((split, name))
    return pos, neg

def main():
    random.seed(42)
    pos_all, neg_all = [], []
    for split in ("train", "val"):
        p, n = collect(split)
        pos_all += p; neg_all += n
    neg_sample = random.sample(neg_all, min(24, len(neg_all)))

    cards, neg_cards = [], []
    for split, name in pos_all:
        out, bw, bh = render(split, name)
        if not out:
            continue
        size_info = f"{bw}×{bh}px" if bw else "?"
        cards.append(f'<div class="card"><img src="{out}"><div class="meta">'
                     f'<b>{split}/{name}</b><br>框 {size_info}</div></div>')
    for split, name in neg_sample:
        out, _, _ = render(split, name)
        if not out:
            continue
        neg_cards.append(f'<div class="card"><img src="{out}"><div class="meta">'
                         f'<b>{split}/{name}</b><br>负样本（应无无人机）</div></div>')

    page = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Tello 自动标注质量抽查 · 2026-10-17</title><style>
body{{font-family:"Microsoft YaHei",sans-serif;background:#111;color:#ddd;margin:20px}}
h1{{font-size:20px}} h2{{font-size:16px;color:#7fd;margin-top:28px}}
p{{font-size:13px;color:#999}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}}
.card{{background:#1c1c1c;border:1px solid #333;border-radius:8px;overflow:hidden}}
.card img{{width:100%;display:block}}
.meta{{padding:6px 10px;font-size:12px;line-height:1.5;color:#bbb}}
.badge{{display:inline-block;background:#a33;color:#fff;border-radius:4px;padding:1px 8px;font-size:12px;margin-left:6px}}
</style></head><body>
<h1>Tello 自动标注质量抽查 <span class="badge">弱监督 YOLO-World 预标注</span></h1>
<p>红框 = 标注的 tello 框。正样本 {len(cards)} 张全量，负样本抽查 {len(neg_cards)} 张。
<br>检查点：① 框是否紧贴无人机（框到背景/人手 → 偏大）② 有框但画面里没无人机 → 假阳性
③ 负样本里藏着无人机 → 假阴性（直接拉低召回，最伤模型）</p>
<h2>正样本（有框）— {len(cards)} 张</h2><div class="grid">{''.join(cards)}</div>
<h2>负样本抽查 — {len(neg_cards)} / {len(neg_all)} 张</h2><div class="grid">{''.join(neg_cards)}</div>
</body></html>"""
    with open(os.path.join(OUT, "review.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print(f"正样本: {len(cards)}  负样本抽查: {len(neg_cards)}/{len(neg_all)}")
    print("输出:", os.path.join(OUT, "review.html"))

if __name__ == "__main__":
    main()
