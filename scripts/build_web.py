#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文献库 — 打包成完全自包含的单文件网页

把 web/ 目录下的网页模板和全部文献数据合并成一个独立的 HTML 文件：
  - CSS、JS、数据全部内联，**零外部请求、零依赖**
  - 双击就能打开，不需要装任何东西、不需要联网
  - 可以直接发到微信群里（就发这一个文件）
  - 同时作为 GitHub Pages 的站点首页

用法：
    python scripts/build_web.py                        # 默认名字
    python scripts/build_web.py --name 玉泉麻醉科文献库.html   # 自定义发送用的文件名
产物：
    index.html                        仓库根目录（**必须**叫这个名，GitHub Pages 才会当首页）
    dist/<名字>.html                   发给同事的那一个文件

模板放哪：`web/` 目录下任意一个 .html 文件（只有一个时自动识别）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
OUT_ROOT = ROOT / "index.html"          # GitHub Pages 强制要求首页名为 index.html
DEFAULT_SEND_NAME = "玉泉医院麻醉科文献库.html"

sys.path.insert(0, str(ROOT / "scripts"))
try:
    import build_index  # 复用同一套数据读取逻辑，保证网页与索引一致
except ImportError as exc:
    sys.exit(f"无法导入 scripts/build_index.py：{exc}")

MARKER = "<!-- LIT_DB_DATA_PLACEHOLDER"


def find_template() -> Path:
    """在 web/ 下找模板；只有一个 .html 时自动识别，名字随便改都不会失效。"""
    if not WEB_DIR.exists():
        sys.exit("找不到 web/ 目录：网页模板应放在 web/ 下")
    candidates = sorted(p for p in WEB_DIR.glob("*.html") if p.is_file())
    if not candidates:
        sys.exit("web/ 下没有 .html 模板文件")
    if len(candidates) > 1:
        names = "、".join(p.name for p in candidates)
        sys.exit(f"web/ 下有多个 html，无法确定用哪个：{names}")
    return candidates[0]


def build() -> tuple[str, int, Path]:
    template = find_template()
    records = build_index.load_papers()
    stats = build_index.compute_stats(records)
    payload = {"count": len(records), "stats": stats, "papers": records}

    # 内联数据；把 </ 转义，避免提前结束 <script> 标签
    data_js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data_js = data_js.replace("</", "<\\/")
    inline = f'<script>window.__LIT_DB_DATA__ = {data_js};</script>\n'

    html = template.read_text(encoding="utf-8")
    if MARKER not in html:
        sys.exit(f"模板结构变了：{template} 里找不到内联数据的插入位置")
    html = html.replace(MARKER, inline + MARKER, 1)
    return html, len(records), template


def main() -> int:
    ap = argparse.ArgumentParser(description="打包自包含网页")
    ap.add_argument("--name", default=DEFAULT_SEND_NAME,
                    help=f"发给同事的文件名，默认 {DEFAULT_SEND_NAME}")
    args = ap.parse_args()

    html, count, template = build()
    out_dist = ROOT / "dist" / args.name
    out_dist.parent.mkdir(exist_ok=True)

    OUT_ROOT.write_text(html, encoding="utf-8", newline="\n")
    out_dist.write_text(html, encoding="utf-8", newline="\n")

    size_kb = len(html.encode("utf-8")) / 1024
    print(f"模板：{template.relative_to(ROOT)}")
    print(f"已打包 {count} 篇文献")
    print(f"  index.html        ({size_kb:.1f} KB)  GitHub Pages 首页（名字不能改）")
    print(f"  dist/{args.name}  ({size_kb:.1f} KB)  发给同事的那一个文件")
    print()
    print("完全自包含：零外部请求，断网可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
