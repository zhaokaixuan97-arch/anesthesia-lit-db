#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文献库 — 打包成单文件网页

把 index.html 和全部文献数据合并成一个独立的 HTML 文件：
  - 双击就能打开，不需要装任何东西、不需要联网
  - 可以直接发到微信群里
  - 也可以原样上传到任意静态托管（阿里云 OSS / Netlify / Cloudflare Pages 等）

用法：
    python scripts/build_web.py
产物：
    dist/index.html              适合上传到静态托管
    dist/麻醉科文献库.html        适合发群里的中文文件名版本
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "index.html"
OUT_DIR = ROOT / "dist"

sys.path.insert(0, str(ROOT / "scripts"))
try:
    import build_index  # 复用同一套数据读取逻辑，保证网页与索引一致
except ImportError as exc:
    sys.exit(f"无法导入 scripts/build_index.py：{exc}")


def main() -> int:
    if not TEMPLATE.exists():
        sys.exit(f"找不到模板：{TEMPLATE}")

    records = build_index.load_papers()
    payload = {"count": len(records), "papers": records}

    # 内联数据；把 </ 转义，避免提前结束 <script> 标签
    data_js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data_js = data_js.replace("</", "<\\/")
    inline = f'<script>window.__LIT_DB_DATA__ = {data_js};</script>\n'

    html = TEMPLATE.read_text(encoding="utf-8")
    marker = "<script>\nconst state = { papers: [] };"
    if marker not in html:
        sys.exit("模板结构变了：找不到内联数据的插入位置。")
    html = html.replace(marker, inline + marker, 1)

    OUT_DIR.mkdir(exist_ok=True)
    out_file = OUT_DIR / "麻醉科文献库.html"
    out_file.write_text(html, encoding="utf-8")

    size_kb = len(html.encode("utf-8")) / 1024
    print(f"已打包 {len(records)} 篇文献")
    print(f"  dist/麻醉科文献库.html  ({size_kb:.1f} KB)")
    print()
    print("本地预览：直接双击 dist/麻醉科文献库.html")
    print("发给同事：把这个文件发到群里即可（微信里点开选浏览器打开）")
    print("要放网上：把仓库根目录的 index.html + index.json 一起上传到静态托管即可")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
