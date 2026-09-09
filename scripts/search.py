#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文献库 — 命令行检索

优先使用 index.sqlite（FTS5）；没有索引时自动回退到直接扫描 YAML。

用法：
    python scripts/search.py "术后谵妄"
    python scripts/search.py "regional anesthesia" --topic 外周神经阻滞
    python scripts/search.py "opioid" --type RCT --year-from 2020 --limit 10
    python scripts/search.py "delirium" --full     # 输出完整摘要与笔记
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("缺少 PyYAML：python -m pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
INDEX_DB = ROOT / "index.sqlite"
PAPERS_DIR = ROOT / "papers"


def fts_query(text: str) -> str:
    """把用户输入转成 FTS5 安全的查询串：每个词加引号，词间 OR/AND 由调用方决定。"""
    terms = [t for t in re.split(r"\s+", text.strip()) if t]
    terms = [t.replace('"', "") for t in terms]
    return " OR ".join(f'"{t}"' for t in terms) if terms else '""'


def _filters(args, prefix: str = "p.") -> tuple[list[str], list]:
    where, params = [], []
    if args.topic:
        where.append(f"{prefix}topics LIKE ?")
        params.append(f"%{args.topic}%")
    if args.type:
        where.append(f"{prefix}type = ?")
        params.append(args.type)
    if args.year_from:
        where.append(f"{prefix}year >= ?")
        params.append(args.year_from)
    if args.year_to:
        where.append(f"{prefix}year <= ?")
        params.append(args.year_to)
    return where, params


def search_sqlite(args) -> list[dict]:
    """先用 FTS5（trigram，支持中文子串）检索；无结果时回退到 LIKE 扫描。"""
    conn = sqlite3.connect(INDEX_DB)
    conn.row_factory = sqlite3.Row
    where, params = _filters(args)
    where_sql = (" AND " + " AND ".join(where)) if where else ""

    rows = []
    try:
        rows = conn.execute(
            f"""
            SELECT p.*, bm25(papers_fts) AS score
            FROM papers_fts f
            JOIN papers p ON p.id = f.id
            WHERE papers_fts MATCH ?{where_sql}
            ORDER BY score
            LIMIT ?
            """,
            [fts_query(args.query)] + params + [args.limit],
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    if not rows:  # 中文短语、短查询等 FTS 覆盖不到的情况
        terms = [t for t in re.split(r"[\s,;，、]+", args.query.strip()) if t]
        if terms:
            like_parts, like_params = [], []
            for t in terms:
                like_parts.append(
                    "(p.title LIKE ? OR p.authors LIKE ? OR p.journal LIKE ? "
                    "OR p.topics LIKE ? OR p.tags LIKE ? OR p.abstract LIKE ? OR p.notes LIKE ?)"
                )
                like_params += [f"%{t}%"] * 7
            like_where = " OR ".join(like_parts)
            rows = conn.execute(
                f"""SELECT p.*, 0 AS score FROM papers p
                    WHERE ({like_where}){where_sql}
                    ORDER BY p.year DESC LIMIT ?""",
                like_params + params + [args.limit],
            ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def search_yaml(args) -> list[dict]:
    """无索引时的回退：简单子串匹配 + 打分。"""
    terms = [t.lower() for t in re.split(r"\s+", args.query.strip()) if t]
    hits = []
    for meta_path in sorted(PAPERS_DIR.glob("*/meta.yaml")):
        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(meta, dict):
            continue
        if args.topic and args.topic not in (meta.get("topics") or []):
            continue
        if args.type and meta.get("type") != args.type:
            continue
        if args.year_from and (meta.get("year") or 0) < args.year_from:
            continue
        if args.year_to and (meta.get("year") or 9999) > args.year_to:
            continue
        notes_path = meta_path.parent / "notes.md"
        notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""
        blob = " ".join([
            str(meta.get("title") or ""), " ".join(meta.get("authors") or []),
            str(meta.get("journal") or ""), " ".join(meta.get("topics") or []),
            " ".join(meta.get("tags") or []), str(meta.get("abstract") or ""), notes,
        ]).lower()
        score = sum(blob.count(t) for t in terms)
        if score:
            rec = dict(meta)
            rec.update({"notes": notes, "score": -score,
                        "authors": ", ".join(meta.get("authors") or []),
                        "topics": ", ".join(meta.get("topics") or [])})
            hits.append(rec)
    hits.sort(key=lambda r: (r.get("score", 0), -(r.get("year") or 0)))
    return hits[: args.limit]


def render(rows: list[dict], full: bool) -> None:
    if not rows:
        print("没有匹配的文献。")
        return
    print(f"命中 {len(rows)} 篇：\n")
    for i, r in enumerate(rows, 1):
        authors = r.get("authors")
        if isinstance(authors, list):
            authors = ", ".join(authors[:3]) + (" 等" if len(authors) > 3 else "")
        year = r.get("year") or "????"
        print(f"{i}. [{year}] {r.get('title')}")
        print(f"   {authors}  |  {r.get('journal') or ''}")
        print(f"   类型: {r.get('type')}   主题: {r.get('topics')}   证据: {r.get('evidence_level')}")
        if r.get("doi"):
            print(f"   DOI: {r['doi']}   https://doi.org/{r['doi']}")
        elif r.get("pmid"):
            print(f"   PMID: {r['pmid']}   https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/")
        if full:
            if r.get("abstract"):
                print(f"   摘要: {str(r['abstract'])[:800]}…")
            notes = r.get("notes") or ""
            if notes:
                print("   笔记:")
                for line in notes.splitlines()[:30]:
                    if line.strip():
                        print(f"     {line}")
        print()
    print("提示：加 --full 可查看摘要与笔记全文。")


def main() -> int:
    ap = argparse.ArgumentParser(description="检索麻醉科文献库")
    ap.add_argument("query", help="关键词（空格分隔，任一命中即返回）")
    ap.add_argument("--topic", help="限定主题")
    ap.add_argument("--type", help="限定文献类型，如 RCT / Guideline")
    ap.add_argument("--year-from", type=int, dest="year_from")
    ap.add_argument("--year-to", type=int, dest="year_to")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--full", action="store_true", help="显示摘要与笔记")
    args = ap.parse_args()

    if INDEX_DB.exists():
        rows = search_sqlite(args)
    else:
        print("（未找到 index.sqlite，改用直接扫描；建议先运行 python scripts/build_index.py）\n")
        rows = search_yaml(args)

    render(rows, args.full)
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
