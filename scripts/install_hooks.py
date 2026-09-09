#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文献库 — 安装 git 提交前钩子

本仓库是 public，一旦 push 出去内容立刻对全网可见。这个钩子会在每次
`git commit` 之前自动跑一遍 validate.py，不通过就**拒绝提交**。

用法（每人装一次）：
    python scripts/install_hooks.py
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = ROOT / ".git" / "hooks"
HOOK = HOOKS_DIR / "pre-commit"

SCRIPT = """#!/bin/sh
# 由 scripts/install_hooks.py 生成：提交前自动校验文献库
ROOT="$(git rev-parse --show-toplevel)"
echo "[pre-commit] 正在校验文献库…"
python "$ROOT/scripts/validate.py" || {
  echo ""
  echo "[pre-commit] 提交被阻止：校验未通过（结构错误或隐私红线告警）。"
  echo "[pre-commit] 修正后重试。确需跳过请用：git commit --no-verify（不推荐）"
  exit 1
}
echo "[pre-commit] 校验通过。"
"""


def main() -> int:
    if not (ROOT / ".git").exists():
        raise SystemExit("当前目录不是 git 仓库，请先在文献库根目录运行。")

    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    if HOOK.exists():
        backup = HOOK.with_suffix(".bak")
        backup.write_text(HOOK.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"已备份原有钩子到 {backup.name}")

    HOOK.write_text(SCRIPT, encoding="utf-8", newline="\n")
    try:
        HOOK.chmod(HOOK.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
    # Windows 上 git 通过 sh 执行钩子，不依赖可执行位
    if os.name == "nt":
        pass

    print(f"已安装 {HOOK.relative_to(ROOT)}")
    print("以后每次 git commit 都会先跑一遍校验；不通过会被拦下。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
