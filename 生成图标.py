# -*- coding: utf-8 -*-
r"""生成小程序图标：teal 圆角方块 + 白色放大镜（镜头里是文字行）。

用法（需要 Pillow，可用打包用的虚拟环境）：
    work\buildenv\Scripts\python.exe 生成图标.py
会在脚本所在目录生成 icon.png（1024，预览用）和 icon.ico（多尺寸，给 exe 用）。
"""
import math
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024
TOP, BOTTOM = (63, 224, 220), (11, 124, 134)
WHITE = (255, 255, 255, 255)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

grad = Image.new("RGB", (1, S))
for y in range(S):
    t = y / (S - 1)
    grad.putpixel((0, y), tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)))
grad = grad.resize((S, S))

mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
img.paste(grad, (0, 0), mask)

d = ImageDraw.Draw(img)

cx, cy = int(S * 0.455), int(S * 0.435)                 # 放大镜圆心
r = int(S * 0.242)                                      # 镜片半径
ring_w = int(S * 0.076)                                 # 镜框粗细
d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=WHITE, width=ring_w)

ang = math.radians(45)                                  # 手柄（45 度，带圆头）
hx = cx + (r + ring_w * 0.15) * math.cos(ang)
hy = cy + (r + ring_w * 0.15) * math.sin(ang)
ex, ey = int(S * 0.815), int(S * 0.815)
handle_w = int(S * 0.086)
d.line([hx, hy, ex, ey], fill=WHITE, width=handle_w)
for px, py in ((hx, hy), (ex, ey)):
    rr = handle_w // 2
    d.ellipse([px - rr, py - rr, px + rr, py + rr], fill=WHITE)

line_w = int(S * 0.034)                                 # 镜片里的三行“文字”
for i, frac in enumerate((0.58, 0.40, 0.50)):
    y = cy - int(S * 0.070) + i * int(S * 0.070)
    half = int(r * frac * 0.82)
    d.rounded_rectangle([cx - half, y - line_w // 2, cx + half, y + line_w // 2],
                        radius=line_w // 2, fill=WHITE)

img.save(os.path.join(HERE, "icon.png"), "PNG")
img.resize((256, 256), Image.LANCZOS).save(
    os.path.join(HERE, "icon.ico"), "ICO",
    sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
)
print("已生成 icon.png 与 icon.ico")
