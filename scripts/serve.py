#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文献库 — 本地网页服务

一条命令启动检索网页（浏览器禁止 file:// 读取 index.json，所以需要这个小服务）。

用法：
    python scripts/serve.py              # 默认 http://127.0.0.1:8765
    python scripts/serve.py --port 9000
    python scripts/serve.py --open       # 启动后自动打开浏览器
    python scripts/serve.py --lan        # 允许局域网访问（手机可连，注意同网段安全）
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socket
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # 安静一点
        sys.stderr.write("  %s\n" % (fmt % args))


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--lan", action="store_true")
    args = ap.parse_args()

    if not (ROOT / "index.json").exists():
        print("提示：还没生成 index.json，先运行 python scripts/build_index.py", file=sys.stderr)

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    handler = functools.partial(Handler, directory=str(ROOT))

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server((host, args.port), handler) as httpd:
        url = f"http://127.0.0.1:{args.port}/index.html"
        print(f"文献库网页已启动：{url}")
        if args.lan:
            print(f"局域网地址（手机可用）：http://{lan_ip()}:{args.port}/index.html")
        print("按 Ctrl+C 停止。")
        if args.open:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
