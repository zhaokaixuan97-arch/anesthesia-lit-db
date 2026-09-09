#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文献库 — 从 CSV 批量导入

用于把已经整理好的文献清单（比如颊针综述的 refs_*.csv）批量入库。
中文文献大多没有 DOI/PMID，走不了 PubMed，所以直接从 CSV 字段建条目。

CSV 期望的列（多余列忽略，缺失列按空处理）：
    ref_no, language, title, authors, journal, year, study_type,
    conditions, doi, id, abstract

用法：
    python scripts/import_csv.py <csv路径> --topic 颊针疗法 --limit 70 --added-by 颊针综述
    python scripts/import_csv.py refs.csv --prefer-keys _resolved.json --limit 70
    python scripts/import_csv.py refs.csv --dry-run        # 只看会导入哪些
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import yaml  # noqa: E402

import fetch_metadata as fm  # noqa: E402

TYPE_MAP = {
    "RCT": "RCT",
    "SR/MA": "Meta-analysis",
    "Systematic review": "Systematic review",
    "Meta-analysis": "Meta-analysis",
    "Review": "Review",
    "Guideline": "Guideline",
    "Case report": "Case report",
    "Case series": "Observational",
    "Mechanism/animal": "Basic science",
    "Bibliometric": "Observational",
    "Observational": "Observational",
    "Other": "Other",
}

LANG_MAP = {"chi": "zh", "zh": "zh", "eng": "en", "en": "en"}


def slug(text: str, max_words: int = 4) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9\s-]", " ", text).lower()
    stop = {"a", "an", "the", "of", "in", "on", "for", "and", "to", "with", "study",
            "effect", "effects", "clinical", "randomized", "controlled", "trial"}
    words = [w for w in text.split() if w and w not in stop]
    return "-".join(words[:max_words])


def split_authors(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    parts = re.split(r"[;,、，]\s*", raw)
    return [p.strip() for p in parts if p.strip()]


def lastname(authors: list[str]) -> str:
    """id 只允许 ASCII：中文作者统一用 cn，避免出现汉字目录名。"""
    if not authors:
        return "cn"
    first = authors[0].strip()
    if re.search(r"[\u4e00-\u9fa5]", first):
        return "cn"
    token = first.split()[0]
    return re.sub(r"[^A-Za-z0-9]", "", token).lower() or "cn"


def pick_topics(row: dict, base_topic: str | None) -> list[str]:
    topics: list[str] = []
    if base_topic:
        topics.append(base_topic)
    cond = (row.get("conditions") or "").lower()
    if "postoperative" in cond or "术后" in cond:
        if "术后镇痛" not in topics:
            topics.append("术后镇痛")
    if "insomnia" in cond or "失眠" in cond:
        if "慢性疼痛" not in topics and not topics:
            topics.append("慢性疼痛")
    return topics or ["颊针疗法"]


def build_record(row: dict, base_topic: str | None, added_by: str, idx: int) -> dict:
    title = (row.get("title") or "").strip()
    if not title:
        raise ValueError("没有标题")
    year_raw = (row.get("year") or "").strip()
    m = re.search(r"(19|20)\d{2}", year_raw)
    year = int(m.group(0)) if m else 0

    authors = split_authors(row.get("authors"))
    ref_no = (row.get("ref_no") or f"row{idx}").strip().lower().replace(" ", "")
    if authors and slug(title):
        ident = f"{year or 'nd'}-{lastname(authors)}-{slug(title)}"
    else:
        ident = f"{year or 'nd'}-cn-{re.sub(r'[^a-z0-9-]', '', ref_no)}"

    doi = (row.get("doi") or "").strip()
    url = f"https://doi.org/{doi}" if doi else (row.get("id") or "").strip()

    return {
        "id": ident,
        "title": title,
        "authors": authors,
        "first_author": authors[0] if authors else "",
        "journal": (row.get("journal") or "").strip(),
        "year": year or None,
        "volume": "",
        "issue": "",
        "pages": "",
        "doi": doi,
        "pmid": "",
        "type": TYPE_MAP.get((row.get("study_type") or "").strip(), "Other"),
        "topics": pick_topics(row, base_topic),
        "tags": [t for t in [(row.get("conditions") or "").strip()] if t],
        "evidence_level": "na",
        "oa_status": "unknown",
        "abstract": (row.get("abstract") or "").strip(),
        "url": url,
        "pdf": None,
        "language": LANG_MAP.get((row.get("language") or "").strip().lower(), "en"),
        "status": "draft",
        "added_by": added_by,
        "added_date": fm._dt.date.today().isoformat(),
        "updated_date": fm._dt.date.today().isoformat(),
        "relevance": 3,
    }


def unique_dir(ident: str) -> Path:
    target = fm.PAPERS_DIR / ident
    n = 2
    while target.exists():
        target = fm.PAPERS_DIR / f"{ident}-{n}"
        n += 1
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description="从 CSV 批量导入文献")
    ap.add_argument("csv_path")
    ap.add_argument("--topic", default=None, help="统一打上的主题")
    ap.add_argument("--limit", type=int, default=0, help="最多导入几篇，0 = 全部")
    ap.add_argument("--added-by", default="CSV 导入")
    ap.add_argument("--prefer-keys", default=None,
                    help="JSON 文件（如 _resolved.json），其中的 ref_no 优先导入")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.csv_path)
    if not src.exists():
        sys.exit(f"找不到文件：{src}")

    with src.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    prefer: set[str] = set()
    if args.prefer_keys:
        pk = Path(args.prefer_keys)
        if pk.exists():
            prefer = {str(v).strip().lower() for v in json.loads(pk.read_text(encoding="utf-8")).values()}

    def score(r: dict) -> tuple:
        ref = (r.get("ref_no") or "").strip().lower()
        return (
            0 if ref in prefer else 1,
            0 if (r.get("doi") or "").strip() else 1,
            0 if (r.get("language") or "").strip().lower().startswith("en") else 1,
            -(int(re.search(r"(19|20)\d{2}", r.get("year") or "0").group(0))
              if re.search(r"(19|20)\d{2}", r.get("year") or "") else 0),
        )

    rows.sort(key=score)
    if args.limit:
        rows = rows[: args.limit]

    created = skipped = 0
    for i, row in enumerate(rows, 1):
        try:
            rec = build_record(row, args.topic, args.added_by, i)
        except ValueError as exc:
            print(f"  跳过 [{(row.get('ref_no') or '?')}]：{exc}", file=sys.stderr)
            skipped += 1
            continue

        if args.dry_run:
            print(f"  [{rec['id']}] {rec['year']} {rec['type']:<12} {rec['title'][:70]}")
            continue

        target = unique_dir(rec["id"])
        rec["id"] = target.name
        target.mkdir(parents=True, exist_ok=True)
        (target / "meta.yaml").write_text(
            yaml.safe_dump(rec, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8", newline="\n",
        )
        (target / "notes.md").write_text(
            fm.render_notes(rec, args.added_by), encoding="utf-8", newline="\n")
        created += 1

    if args.dry_run:
        print(f"\n（试运行）将导入 {len(rows)} 篇，跳过 {skipped} 篇")
    else:
        print(f"已导入 {created} 篇，跳过 {skipped} 篇")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
