#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文献库 — 生成网页二维码

生成一张带标题的二维码图片，发到微信群里，同事扫码即可打开文献库网页。

用法：
    python scripts/make_qr.py
产物：
    assets/二维码.png
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import qrcode
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("缺少依赖：python -m pip install qrcode pillow")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "二维码.png"

# 线上地址（改成你自己的域名/地址时只改这一行）
URL = "https://zhaokaixuan97-arch.github.io/anesthesia-lit-db/"
TITLE = "麻醉科文献库"

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",   # 微软雅黑 Bold
    r"C:\Windows\Fonts\msyh.ttc",     # 微软雅黑
    r"C:\Windows\Fonts\simhei.ttf",   # 黑体
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def main() -> int:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(URL)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="#0b5a8a", back_color="white").convert("RGB")

    w, h = qr_img.size
    pad_x, pad_top, pad_bottom = 40, 30, 34
    title_font = load_font(46)
    url_font = load_font(20)

    # 先量文字宽度，保证画布够宽、网址不被裁掉
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    def text_width(text: str, font) -> int:
        box = probe.textbbox((0, 0), text, font=font)
        return box[2] - box[0]

    url_display = URL.replace("https://", "")
    text_w = max(text_width(TITLE, title_font),
                 text_width("扫码查看文献库", url_font),
                 text_width(url_display, url_font))
    canvas_w = max(w + pad_x * 2, text_w + pad_x * 2)

    canvas = Image.new("RGB", (canvas_w, h + pad_top + pad_bottom + 116), "white")
    canvas.paste(qr_img, ((canvas_w - w) // 2, pad_top))
    draw = ImageDraw.Draw(canvas)

    def centered(text: str, y: int, font) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(((canvas.width - (bbox[2] - bbox[0])) / 2, y), text,
                  fill="#16191d", font=font)

    y = pad_top + h + 22
    centered(TITLE, y, title_font)
    centered("扫码查看文献库", y + 58, url_font)
    centered(url_display, y + 88, url_font)

    OUT.parent.mkdir(exist_ok=True)
    canvas.save(OUT)
    print(f"已生成 {OUT.relative_to(ROOT)}  ({canvas.width}×{canvas.height})")
    print(f"指向：{URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
