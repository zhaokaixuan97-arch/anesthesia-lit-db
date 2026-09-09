#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文献库 — 元数据采集脚本（零外部依赖，仅需 requests + PyYAML）

用法：
    # 1) 按关键词检索候选文献
    python scripts/fetch_metadata.py search "postoperative delirium elderly" --limit 8

    # 2) 只看某篇的元数据（DOI 或 PMID 都行）
    python scripts/fetch_metadata.py resolve 10.1056/NEJMoa2210000
    python scripts/fetch_metadata.py resolve 38000000

    # 3) 正式入库：自动建目录、写 meta.yaml 和 notes.md 模板
    python scripts/fetch_metadata.py add 10.1056/NEJMoa2210000 \
        --topics 术后谵妄与认知功能障碍 --added-by 张三 --relevance 5

数据来源：PubMed E-utilities + NCBI ID Converter + Crossref（免费，无需 API key）。
只写入公开出版物信息，不涉及任何患者数据。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("缺少 PyYAML，请先运行：python -m pip install pyyaml")

try:
    import requests
except ImportError:
    sys.exit("缺少 requests，请先运行：python -m pip install requests")

ROOT = Path(__file__).resolve().parent.parent
PAPERS_DIR = ROOT / "papers"
TOPICS_FILE = ROOT / "schema" / "topics.yaml"

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
IDCONV = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
CROSSREF = "https://api.crossref.org/works/"

# NCBI 建议带上工具名和联系邮箱，方便他们联系你（可自行改成你的邮箱）
TOOL = "anesthesia-lit-db"
EMAIL = "anesthesia-lit-db@example.org"
_LAST_CALL = [0.0]
MIN_INTERVAL = 0.35  # NCBI 无 API key 时限速 3 次/秒


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def _throttle() -> None:
    dt = time.time() - _LAST_CALL[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _LAST_CALL[0] = time.time()


_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": f"{TOOL} (mailto:{EMAIL})"})


def _get(url: str, params: dict | None = None, retries: int = 5) -> bytes:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            _throttle()
            resp = _SESSION.get(url, params=params, timeout=45)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"请求失败：{url}\n{last_err}")


def _get_json(url: str, params: dict | None = None) -> dict:
    return json.loads(_get(url, params).decode("utf-8", "replace"))


# --------------------------------------------------------------------------
# DOI / PMID 互转
# --------------------------------------------------------------------------
def doi_to_pmid(doi: str) -> str | None:
    data = _get_json(IDCONV, {"ids": doi, "format": "json", "tool": TOOL, "email": EMAIL})
    records = data.get("records") or []
    if records:
        return str(records[0].get("pmid") or "") or None
    return None


def normalise_id(raw: str) -> tuple[str | None, str | None]:
    """返回 (pmid, doi)。输入可以是 DOI 或 PMID。"""
    raw = raw.strip()
    if re.fullmatch(r"\d{6,9}", raw):
        return raw, None
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", raw, flags=re.I)
    if doi.lower().startswith("doi:"):
        doi = doi[4:].strip()
    return doi_to_pmid(doi), doi


# --------------------------------------------------------------------------
# PubMed 取元数据
# --------------------------------------------------------------------------
PUBTYPE_MAP = [
    ("randomized controlled trial", "RCT"),
    ("meta-analysis", "Meta-analysis"),
    ("systematic review", "Systematic review"),
    ("practice guideline", "Guideline"),
    ("guideline", "Guideline"),
    ("case reports", "Case report"),
    ("review", "Review"),
    ("editorial", "Editorial"),
    ("comment", "Editorial"),
]

GUIDELINE_WORDS = ("guideline", "consensus", "recommendations", "position statement",
                   "practice advisory", "expert consensus")
RCT_WORDS = ("randomized controlled", "randomised controlled", "randomized trial",
             "randomised trial", "randomized clinical trial", "a randomized", "a randomised")


def classify(pubtypes: list[str], title: str = "") -> str:
    """先看 PubMed 的出版类型，再用标题兜底（指南/共识常只被标为 Journal Article）。"""
    lowered = [p.lower() for p in pubtypes]
    t = (title or "").lower()

    if any("meta-analysis" in p for p in lowered) or "meta-analysis" in t:
        return "Meta-analysis"
    if any("systematic review" in p for p in lowered) or "systematic review" in t:
        return "Systematic review"
    if any("practice guideline" in p or "guideline" in p for p in lowered) \
            or any(w in t for w in GUIDELINE_WORDS):
        return "Guideline"
    if any("randomized controlled trial" in p for p in lowered) \
            or any(w in t for w in RCT_WORDS):
        return "RCT"
    if any("case reports" in p for p in lowered) or "case report" in t:
        return "Case report"
    if any(p in ("editorial", "comment") for p in lowered):
        return "Editorial"
    if any("review" in p for p in lowered) or "review" in t:
        return "Review"
    if any(k in p for p in lowered for k in ("observational", "cohort", "comparative")):
        return "Observational"
    return "Other"


def esearch(term: str, limit: int) -> list[str]:
    data = _get_json(
        f"{EUTILS}/esearch.fcgi",
        {
            "db": "pubmed",
            "term": term,
            "retmode": "json",
            "retmax": str(limit),
            "sort": "relevance",
            "tool": TOOL,
            "email": EMAIL,
        },
    )
    return data.get("esearchresult", {}).get("idlist", [])


def esummary(pmid: str) -> dict:
    data = _get_json(
        f"{EUTILS}/esummary.fcgi",
        {"db": "pubmed", "id": pmid, "retmode": "json", "tool": TOOL, "email": EMAIL},
    )
    result = data.get("result", {})
    if pmid not in result:
        raise RuntimeError(f"PubMed 未找到 PMID {pmid}")
    return result[pmid]


def efetch_abstract(pmid: str) -> str:
    xml = _get(f"{EUTILS}/efetch.fcgi", {
        "db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "xml",
        "tool": TOOL, "email": EMAIL,
    })
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ""
    chunks: list[str] = []
    for node in root.iter("AbstractText"):
        label = node.attrib.get("Label")
        text = "".join(node.itertext()).strip()
        if not text:
            continue
        chunks.append(f"{label}: {text}" if label else text)
    return "\n".join(chunks).strip()


def crossref(doi: str) -> dict:
    try:
        msg = _get_json(CROSSREF + urllib.parse.quote(doi)).get("message", {})
    except Exception:  # noqa: BLE001
        return {}
    authors = []
    for a in msg.get("author", []) or []:
        family = a.get("family") or ""
        given = (a.get("given") or "").strip()
        initials = "".join(p[0] for p in re.split(r"[\s.\-]+", given) if p)
        if family:
            authors.append(f"{family} {initials}".strip())
    year = None
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (msg.get(key) or {}).get("date-parts") or [[]]
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break
    return {
        "title": (msg.get("title") or [""])[0],
        "authors": authors,
        "journal": (msg.get("container-title") or [""])[0],
        "year": year,
        "volume": msg.get("volume", ""),
        "issue": msg.get("issue", ""),
        "pages": msg.get("page", ""),
        "doi": msg.get("DOI", doi),
    }


# --------------------------------------------------------------------------
# 生成 meta.yaml
# --------------------------------------------------------------------------
def slugify(text: str, max_words: int = 3) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9\s-]", " ", text).lower()
    stop = {"a", "an", "the", "of", "in", "on", "for", "and", "to", "with",
            "effect", "effects", "impact", "role", "study", "trial", "randomized",
            "randomised", "controlled", "versus", "vs", "among", "after", "before",
            "during", "patients", "patient", "outcomes", "outcome"}
    words = [w for w in text.split() if w and w not in stop]
    return "-".join(words[:max_words]) or "untitled"


def first_author_lastname(authors: list[str]) -> str:
    if not authors:
        return "anon"
    return re.sub(r"[^a-z0-9]", "", authors[0].split()[0].lower()) or "anon"


def build_record(raw_id: str, topics: list[str], added_by: str, relevance: int) -> dict:
    pmid, doi = normalise_id(raw_id)
    base: dict = {}
    if pmid:
        summary = esummary(pmid)
        doi = doi or next(
            (a["value"] for a in summary.get("articleids", []) if a.get("idtype") == "doi"),
            None,
        )
        authors = [a["name"] for a in summary.get("authors", []) if a.get("authtype") == "Author"]
        pubdate = summary.get("pubdate") or ""
        year_match = re.search(r"(19|20)\d{2}", pubdate)
        base = {
            "title": (summary.get("title") or "").rstrip("."),
            "authors": authors,
            "journal": summary.get("fulljournalname") or summary.get("source") or "",
            "year": int(year_match.group(0)) if year_match else None,
            "volume": summary.get("volume", ""),
            "issue": summary.get("issue", ""),
            "pages": summary.get("pages", ""),
            "doi": doi,
            "pmid": pmid,
            "type": classify(summary.get("pubtype", []) or [], summary.get("title") or ""),
            "abstract": efetch_abstract(pmid),
            "pmcid": next(
                (a["value"] for a in summary.get("articleids", []) if a.get("idtype") == "pmc"),
                None,
            ),
        }
    elif doi:
        base = crossref(doi)
        base.update({"pmid": None, "type": "Other", "abstract": "", "pmcid": None})
    else:
        raise RuntimeError(f"无法解析标识符：{raw_id}")

    if not base.get("title"):
        raise RuntimeError(f"未取到标题：{raw_id}")

    year = base.get("year") or _dt.date.today().year
    ident = f"{year}-{first_author_lastname(base.get('authors') or [])}-{slugify(base['title'])}"
    today = _dt.date.today().isoformat()

    return {
        "id": ident,
        "title": base["title"],
        "authors": base.get("authors") or [],
        "first_author": (base.get("authors") or [""])[0],
        "journal": base.get("journal") or "",
        "year": year,
        "volume": str(base.get("volume") or ""),
        "issue": str(base.get("issue") or ""),
        "pages": str(base.get("pages") or ""),
        "doi": base.get("doi") or "",
        "pmid": str(base.get("pmid") or ""),
        "type": base.get("type") or "Other",
        "topics": topics,
        "tags": [],
        "evidence_level": "na",
        "oa_status": "open" if base.get("pmcid") else "unknown",
        "abstract": base.get("abstract") or "",
        "url": f"https://doi.org/{base['doi']}" if base.get("doi") else
               (f"https://pubmed.ncbi.nlm.nih.gov/{base['pmid']}/" if base.get("pmid") else ""),
        "pdf": None,
        "language": "en",
        "status": "draft",
        "added_by": added_by,
        "added_date": today,
        "updated_date": today,
        "relevance": relevance,
    }


NOTES_TEMPLATE = """# {title}

> {journal} · {year} · {type}
> 自动生成于 {date}。**请人工补充下面各节**，未填写的保留「待补充」。
> 提醒：本文件是公开出版物笔记，**严禁写入任何患者信息**（姓名、住院号、影像、病例细节）。

## 摘要

{abstract}

## 一句话结论

待补充

## PICO

- **P**（人群）：
- **I**（干预）：
- **C**（对照）：
- **O**（结局）：

## 主要结果

待补充（关键数字、效应量、置信区间）

## 局限性

待补充

## 对本科室的实践意义

待补充（是否改变现有流程？适用哪类患者？成本/可行性如何？）

## 引用格式

待补充

## 变更记录

- {date} 由 {added_by} 创建
"""


def render_notes(record: dict, added_by: str) -> str:
    abstract = (record.get("abstract") or "").strip() or "（未取到摘要，请手动补充）"
    return NOTES_TEMPLATE.format(
        title=record.get("title", ""),
        journal=record.get("journal") or "—",
        year=record.get("year") or "—",
        type=record.get("type") or "—",
        abstract=abstract,
        date=_dt.date.today().isoformat(),
        added_by=added_by,
    )


def _load_topic_leaves() -> set[str]:
    data = yaml.safe_load(TOPICS_FILE.read_text(encoding="utf-8")) or {}
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


def cmd_search(args) -> int:
    ids = esearch(args.query, args.limit)
    if not ids:
        print("没有检索到结果。")
        return 1
    print(f"共 {len(ids)} 条候选（PubMed）：\n")
    for pmid in ids:
        s = esummary(pmid)
        year = (re.search(r"(19|20)\d{2}", s.get("pubdate") or "") or [""])[0] if re.search(r"(19|20)\d{2}", s.get("pubdate") or "") else "????"
        print(f"  PMID {pmid}  [{year}] {s.get('source','')}")
        print(f"    {s.get('title','').rstrip('.')}")
        doi = next((a["value"] for a in s.get("articleids", []) if a.get("idtype") == "doi"), "")
        if doi:
            print(f"    DOI: {doi}")
        print()
    return 0


def cmd_resolve(args) -> int:
    record = build_record(args.identifier, [], "unknown", 3)
    print(yaml.safe_dump(record, allow_unicode=True, sort_keys=False, width=100))
    return 0


def cmd_add(args) -> int:
    topics = [t.strip() for t in (args.topics or "").split(",") if t.strip()]
    leaves = _load_topic_leaves()
    bad = [t for t in topics if t not in leaves]
    if bad:
        print("以下主题不在 schema/topics.yaml 的叶子主题中：", file=sys.stderr)
        for t in bad:
            print(f"  - {t}", file=sys.stderr)
        print("请先确认主题名，或在 topics.yaml 中新增。", file=sys.stderr)
        return 2
    if not topics:
        print("警告：未指定 --topics，录入后请尽快补上。", file=sys.stderr)

    record = build_record(args.identifier, topics, args.added_by, args.relevance)
    target = PAPERS_DIR / record["id"]
    if target.exists() and not args.force:
        print(f"目录已存在：{target}\n如需覆盖请加 --force", file=sys.stderr)
        return 3
    target.mkdir(parents=True, exist_ok=True)
    (target / "meta.yaml").write_text(
        yaml.safe_dump(record, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    (target / "notes.md").write_text(render_notes(record, args.added_by), encoding="utf-8")
    print(f"已创建 {target}")
    print(f"  meta.yaml  ({record['type']}, {record['year']}, {record['journal']})")
    print("  notes.md   请人工补充 PICO 与结论")
    return 0


def cmd_regen_notes(args) -> int:
    """按当前模板重新生成 notes.md（保留已有人工内容到 .bak）。"""
    count = 0
    for meta_path in sorted(PAPERS_DIR.glob("*/meta.yaml")):
        try:
            record = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            print(f"跳过 {meta_path}: {exc}", file=sys.stderr)
            continue
        notes_path = meta_path.parent / "notes.md"
        if notes_path.exists() and not args.force:
            if "待补充" not in notes_path.read_text(encoding="utf-8"):
                print(f"跳过（已有实质内容）{notes_path}", file=sys.stderr)
                continue
        if notes_path.exists():
            notes_path.with_suffix(".md.bak").write_text(
                notes_path.read_text(encoding="utf-8"), encoding="utf-8")
        notes_path.write_text(
            render_notes(record, record.get("added_by") or "unknown"), encoding="utf-8")
        count += 1
    print(f"已重新生成 {count} 个 notes.md")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="麻醉科文献库元数据采集")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="按关键词检索 PubMed")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=8)
    s.set_defaults(func=cmd_search)

    r = sub.add_parser("resolve", help="查看单篇元数据（DOI 或 PMID）")
    r.add_argument("identifier")
    r.set_defaults(func=cmd_resolve)

    a = sub.add_parser("add", help="正式入库")
    a.add_argument("identifier", help="DOI 或 PMID")
    a.add_argument("--topics", default="", help="逗号分隔的叶子主题")
    a.add_argument("--added-by", default="unknown")
    a.add_argument("--relevance", type=int, default=3, choices=range(1, 6))
    a.add_argument("--force", action="store_true")
    a.set_defaults(func=cmd_add)

    rn = sub.add_parser("regen-notes", help="按当前模板重新生成 notes.md（含摘要）")
    rn.add_argument("--force", action="store_true", help="即使已有实质内容也覆盖")
    rn.set_defaults(func=cmd_regen_notes)

    args = p.parse_args()
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
