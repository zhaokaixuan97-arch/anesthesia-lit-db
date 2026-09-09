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

import datetime as _dt
import json
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


def build_json(records: list[dict]) -> None:
    payload = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "count": len(records),
        "papers": records,
    }
    INDEX_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def main() -> int:
    records = load_papers()
    build_json(records)
    build_sqlite(records)
    print(f"已索引 {len(records)} 篇文献")
    print(f"  {INDEX_JSON.relative_to(ROOT)}  ({INDEX_JSON.stat().st_size/1024:.1f} KB)")
    print(f"  {INDEX_DB.relative_to(ROOT)}  ({INDEX_DB.stat().st_size/1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
