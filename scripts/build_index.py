#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文献库 — 索引构建

把 papers/*/meta.yaml + notes.md 汇总成两个产物：
  index.json    供网页（web/index.html）和外部程序读取
  index.sqlite  SQLite FTS5 全文索引，供 search.py 和 MCP server 快速检索

用法：
    python scripts/build_index.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("缺少 PyYAML：python -m pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
PAPERS_DIR = ROOT / "papers"
INDEX_JSON = ROOT / "index.json"
INDEX_DB = ROOT / "index.sqlite"

FIELDS = ["id", "title", "authors", "first_author", "journal", "year", "volume",
          "issue", "pages", "doi", "pmid", "type", "topics", "tags",
          "evidence_level", "oa_status", "url", "status", "added_by",
          "added_date", "updated_date", "relevance", "abstract"]

# GitHub 官方建议单个仓库保持在 1GB 以内
LIMIT_BYTES = 1024 ** 3


def _dir_bytes(path: Path) -> tuple[int, int]:
    total = files = 0
    if not path.exists():
        return 0, 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
                files += 1
            except OSError:
                pass
    return total, files


def compute_stats(records: list[dict]) -> dict:
    """容量仪表盘数据。

    文献数据体积在本地精确计算；仓库总体积（含 git 历史）由 CI 通过
    GitHub API 提供（环境变量 REPO_SIZE_KB，单位 KB），拿不到时用估算值。
    """
    papers_bytes, papers_files = _dir_bytes(PAPERS_DIR)
    index_bytes = INDEX_JSON.stat().st_size if INDEX_JSON.exists() else 0

    n = len(records)
    avg = int(papers_bytes / n) if n else 0

    repo_bytes = 0
    raw = (os.environ.get("REPO_SIZE_KB") or "").strip()
    if raw.isdigit():
        repo_bytes = int(raw) * 1024

    # 仓库小于 1MB 时用本地估算：GitHub 报告的体积每次 push 都会变，
    # 写进产物会让机器人每次都多提交一次，还会和人工推送抢跑（non-fast-forward）。
    # 等仓库真的超过 1MB，说明文献量已经上来了，那时再用精确值。
    if repo_bytes >= 1_000_000:
        # 用「仓库体积 / 篇数」估算每篇实际增量（含索引、网页与 git 历史）
        per_paper = max(repo_bytes // n, 1) if n else 1
        basis = "repo"
        used_bytes = repo_bytes
    else:
        # 本地估算：每篇约占文本体积的 6 倍（索引 + 网页 + 历史）
        per_paper = max(avg * 6, 1024)
        basis = "estimate"
        used_bytes = papers_bytes + index_bytes
        repo_bytes = 0  # 清掉，保证产物完全确定（否则每次 push 都会变）

    remaining = max(LIMIT_BYTES - used_bytes, 0)
    return {
        "papers": n,
        "papers_files": papers_files,
        "papers_bytes": papers_bytes,
        "index_bytes": index_bytes,
        "avg_bytes": avg,
        "repo_bytes": repo_bytes,
        "per_paper_bytes": per_paper,
        "limit_bytes": LIMIT_BYTES,
        "used_bytes": used_bytes,
        "used_pct": round(used_bytes / LIMIT_BYTES * 100, 3),
        "remaining_bytes": remaining,
        "remaining_papers": remaining // per_paper,
        "basis": basis,
    }


def load_papers() -> list[dict]:
    records = []
    for meta_path in sorted(PAPERS_DIR.glob("*/meta.yaml")):
        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            print(f"跳过（YAML 错误）{meta_path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(meta, dict):
            continue
        rec = {k: meta.get(k) for k in FIELDS}
        rec["id"] = rec["id"] or meta_path.parent.name
        notes_path = meta_path.parent / "notes.md"
        rec["notes"] = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""
        rec["path"] = str(meta_path.parent.relative_to(ROOT)).replace("\\", "/")
        records.append(rec)
    records.sort(key=lambda r: (-(r.get("year") or 0), r.get("first_author") or ""))
    return records


def build_sqlite(records: list[dict]) -> None:
    if INDEX_DB.exists():
        INDEX_DB.unlink()
    conn = sqlite3.connect(INDEX_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE papers (
            id TEXT PRIMARY KEY,
            title TEXT, authors TEXT, journal TEXT, year INTEGER,
            type TEXT, topics TEXT, tags TEXT, doi TEXT, pmid TEXT,
            evidence_level TEXT, url TEXT, status TEXT, relevance INTEGER,
            added_date TEXT, abstract TEXT, notes TEXT, path TEXT
        );
        CREATE VIRTUAL TABLE papers_fts USING fts5(
            id UNINDEXED, title, authors, journal, topics, tags,
            abstract, notes, tokenize='trigram'
        );
        """
    )
    for r in records:
        conn.execute(
            "INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["id"], r.get("title") or "", ", ".join(r.get("authors") or []),
                r.get("journal") or "", r.get("year"), r.get("type") or "",
                ", ".join(r.get("topics") or []), ", ".join(r.get("tags") or []),
                r.get("doi") or "", r.get("pmid") or "", r.get("evidence_level") or "",
                r.get("url") or "", r.get("status") or "", r.get("relevance"),
                r.get("added_date") or "", r.get("abstract") or "",
                r.get("notes") or "", r.get("path") or "",
            ),
        )
        conn.execute(
            "INSERT INTO papers_fts (id, title, authors, journal, topics, tags, abstract, notes) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                r["id"], r.get("title") or "", ", ".join(r.get("authors") or []),
                r.get("journal") or "", ", ".join(r.get("topics") or []),
                ", ".join(r.get("tags") or []), r.get("abstract") or "", r.get("notes") or "",
            ),
        )
    conn.commit()
    conn.close()


def build_json(records: list[dict], stats: dict) -> None:
    # 刻意不写入生成时间戳：保持输出确定性，这样 GitHub Actions 重建后
    # 只有在内容真的变化时才会产生提交，避免每次推送都多一个机器人提交。
    payload = {
        "count": len(records),
        "stats": stats,
        "papers": records,
    }
    INDEX_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
    )


def main() -> int:
    records = load_papers()
    stats = compute_stats(records)
    build_json(records, stats)
    build_sqlite(records)
    print(f"已索引 {len(records)} 篇文献")
    print(f"  {INDEX_JSON.relative_to(ROOT)}  ({INDEX_JSON.stat().st_size/1024:.1f} KB)")
    print(f"  {INDEX_DB.relative_to(ROOT)}  ({INDEX_DB.stat().st_size/1024:.1f} KB)")
    print(f"  文献数据 {stats['papers_bytes']/1024:.1f} KB"
          f"（上限 1GB，已用 {stats['used_pct']}%，约可再放 {stats['remaining_papers']:,} 篇）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
