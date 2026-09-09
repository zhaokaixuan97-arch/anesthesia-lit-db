#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文献库 — 处理「添加文献」Issue

由 .github/workflows/add-paper.yml 调用。从 Issue 正文里解析出 DOI/PMID，
调用 fetch_metadata 抓题录并写入 papers/，最后写一份处理结果到 .issue-result.md。

安全设计（仓库是公开的，任何人都能提交 Issue）：
  - **只信任 DOI/PMID 字段**，且必须匹配严格正则，绝不把原始输入拼进 shell
  - 主题必须来自 schema/topics.yaml 的白名单
  - 备注**不写入仓库**，只回显在 Issue 评论里
  - 写完后跑一次 validate.py，命中隐私红线就整个回滚

环境变量：
    ISSUE_BODY     Issue 正文
    ISSUE_AUTHOR   提交者 GitHub 用户名
    ISSUE_NUMBER   Issue 编号
    ISSUE_URL      Issue 链接
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import yaml  # noqa: E402

import fetch_metadata as fm  # noqa: E402

RESULT_FILE = ROOT / ".issue-result.md"

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
PMID_RE = re.compile(r"^\d{6,9}$")


def parse_form(body: str) -> dict[str, str]:
    """把 Issue 表单正文按 `### 字段名` 切成字典。"""
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for line in (body or "").splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            current = m.group(1).strip()
            fields[current] = []
        elif current is not None:
            fields[current].append(line)
    return {k: "\n".join(v).strip() for k, v in fields.items()}


def pick(fields: dict[str, str], *keywords: str) -> str:
    for label, value in fields.items():
        if any(k in label for k in keywords):
            return value.strip()
    return ""


def clean_identifier(raw: str) -> str | None:
    """从用户输入里提取合法标识符；不合法一律返回 None。"""
    text = (raw or "").strip()
    text = text.split()[0] if text else ""
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text, flags=re.I)
    text = re.sub(r"^doi:\s*", "", text, flags=re.I)
    text = text.strip().rstrip(".,;)")
    if PMID_RE.match(text) or DOI_RE.match(text):
        return text
    return None


def topic_leaves() -> set[str]:
    data = yaml.safe_load((ROOT / "schema" / "topics.yaml").read_text(encoding="utf-8")) or {}
    leaves: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, str):
                    leaves.add(v)
                else:
                    walk(v)

    walk(data.get("topics") or {})
    return leaves


def write_result(ok: bool, lines: list[str]) -> None:
    icon = "✅" if ok else "❌"
    RESULT_FILE.write_text(f"## {icon} 处理结果\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"{icon} " + " | ".join(lines))


def fail(reason: str) -> int:
    write_result(False, [
        reason,
        "",
        "如果你确认 DOI/PMID 没问题，也可以在本机用命令行手动入库：",
        "```bash",
        "python scripts/fetch_metadata.py add <DOI或PMID> --topics \"主题\"",
        "```",
    ])
    return 1


def main() -> int:
    body = os.environ.get("ISSUE_BODY", "")
    author = (os.environ.get("ISSUE_AUTHOR") or "unknown").strip()
    issue_no = os.environ.get("ISSUE_NUMBER", "")
    issue_url = os.environ.get("ISSUE_URL", "")

    fields = parse_form(body)
    raw_id = pick(fields, "DOI", "PMID")
    raw_topic = pick(fields, "主题")
    note = pick(fields, "备注")

    ident = clean_identifier(raw_id)
    if not ident:
        return fail(f"没能从表单里解析出合法的 DOI 或 PMID（收到：`{raw_id[:80]}`）。")

    topics: list[str] = []
    leaves = topic_leaves()
    if raw_topic and raw_topic not in ("（不指定）", "不指定", "无"):
        if raw_topic in leaves:
            topics.append(raw_topic)
        else:
            return fail(f"主题 `{raw_topic}` 不在 schema/topics.yaml 的白名单里，已拒绝。")

    try:
        record = fm.build_record(ident, topics, f"@{author}", 3)
    except RuntimeError as exc:
        return fail(f"从 PubMed/Crossref 抓取失败：{exc}")

    target: Path = fm.PAPERS_DIR / record["id"]
    if target.exists():
        write_result(False, [
            f"这篇已经在库里了：**{record['title']}**",
            "",
            f"- 目录：`papers/{record['id']}`",
            f"- DOI：`{record.get('doi') or '—'}`",
            "",
            "如需补充笔记，请直接编辑该目录下的 `notes.md`。",
        ])
        return 0

    target.mkdir(parents=True, exist_ok=True)
    (target / "meta.yaml").write_text(
        yaml.safe_dump(record, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    (target / "notes.md").write_text(fm.render_notes(record, f"@{author}"), encoding="utf-8")

    # 写完立刻做一次隐私/结构体检；红线告警就整个回滚
    check = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate.py")],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    if check.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        return fail(
            "抓到的内容没通过隐私/结构校验，已拒绝入库（可能是元数据异常）。\n\n"
            "```\n" + (check.stdout or check.stderr)[-800:] + "\n```"
        )

    lines = [
        f"已入库：**{record['title']}**",
        "",
        f"- 作者：{', '.join(record['authors'][:3])}{' 等' if len(record['authors']) > 3 else ''}",
        f"- 来源：{record['journal']} {record['year']}",
        f"- 类型：{record['type']}",
        f"- 主题：{'、'.join(record['topics']) if record['topics'] else '（未指定，可后补）'}",
        f"- DOI：`{record.get('doi') or '—'}`",
        f"- 目录：`papers/{record['id']}`",
        "",
        "网页会在 1 分钟内自动更新。笔记是模板，欢迎直接编辑 `notes.md` 补充。",
    ]
    if note:
        safe_note = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", note)[:500]
        lines += ["", f"> 提交者备注（未写入仓库）：{safe_note}"]

    write_result(True, lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
