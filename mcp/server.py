#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文献库 — MCP (Model Context Protocol) 服务端

零第三方依赖，只用 Python 标准库。通过 stdio 与 AI 客户端通信，
让任何支持 MCP 的客户端（Claude Desktop / Cursor / DeepSeek 等）都能检索本库。

暴露的工具：
    search_literature  按关键词/主题/类型/年份检索
    get_paper          取单篇的完整元数据与笔记
    list_topics        列出主题树及各自文献数
    library_stats      文献库统计
    find_related       找与某篇主题相近的其他文献

自测（不需要客户端）：
    python mcp/server.py --selftest

在客户端里配置（以 Claude Desktop 为例，claude_desktop_config.json）：
    {
      "mcpServers": {
        "anesthesia-lit-db": {
          "command": "python",
          "args": ["D:\\\\...\\\\anesthesia-lit-db\\\\mcp\\\\server.py"]
        }
      }
    }
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path

try:
    import yaml
except ImportError:
    print("缺少 PyYAML：python -m pip install pyyaml", file=sys.stderr)
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent.parent
PAPERS_DIR = ROOT / "papers"
TOPICS_FILE = ROOT / "schema" / "topics.yaml"

SERVER_NAME = "anesthesia-lit-db"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2024-11-05"


# --------------------------------------------------------------------------
# 数据加载
# --------------------------------------------------------------------------
class Library:
    def __init__(self, papers_dir: Path) -> None:
        self.papers_dir = papers_dir
        self._records: list[dict] | None = None
        self._stamp: float = 0.0

    def _newest_mtime(self) -> float:
        newest = 0.0
        if not self.papers_dir.exists():
            return newest
        for p in self.papers_dir.glob("*/*"):
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                pass
        return newest

    def records(self) -> list[dict]:
        stamp = self._newest_mtime()
        if self._records is not None and stamp <= self._stamp:
            return self._records
        out: list[dict] = []
        for meta_path in sorted(self.papers_dir.glob("*/meta.yaml")):
            try:
                meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(meta, dict):
                continue
            meta["id"] = meta.get("id") or meta_path.parent.name
            notes = meta_path.parent / "notes.md"
            meta["_notes"] = notes.read_text(encoding="utf-8") if notes.exists() else ""
            meta["_path"] = str(meta_path.parent.relative_to(ROOT)).replace("\\", "/")
            out.append(meta)
        self._records, self._stamp = out, stamp
        return out

    def by_id(self, pid: str) -> dict | None:
        pid = pid.strip()
        for r in self.records():
            if r["id"] == pid:
                return r
        lowered = pid.lower()
        for r in self.records():
            if r["id"].lower() == lowered:
                return r
        return None


LIB = Library(PAPERS_DIR)


def topic_tree() -> dict:
    try:
        data = yaml.safe_load(TOPICS_FILE.read_text(encoding="utf-8")) or {}
        return data.get("topics") or {}
    except Exception:  # noqa: BLE001
        return {}


def leaves_of(tree: dict) -> list[str]:
    out: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, str):
                    out.append(v)
                else:
                    walk(v)

    walk(tree)
    return out


# --------------------------------------------------------------------------
# 检索
# --------------------------------------------------------------------------
def _terms(query: str) -> list[str]:
    return [t.lower() for t in re.split(r"[\s,;，、]+", query.strip()) if t]


def _blob(rec: dict) -> dict[str, str]:
    return {
        "title": str(rec.get("title") or "").lower(),
        "topics": " ".join(rec.get("topics") or []).lower(),
        "tags": " ".join(rec.get("tags") or []).lower(),
        "abstract": str(rec.get("abstract") or "").lower(),
        "notes": str(rec.get("_notes") or "").lower(),
        "authors": " ".join(rec.get("authors") or []).lower(),
        "journal": str(rec.get("journal") or "").lower(),
    }


WEIGHTS = {"title": 12, "topics": 8, "tags": 6, "abstract": 3,
           "notes": 4, "authors": 2, "journal": 2}


def search(query: str, topic: str | None = None, doc_type: str | None = None,
           year_from: int | None = None, year_to: int | None = None,
           limit: int = 8) -> list[dict]:
    terms = _terms(query)
    scored: list[tuple[float, dict]] = []
    for rec in LIB.records():
        if topic and topic not in (rec.get("topics") or []):
            continue
        if doc_type and rec.get("type") != doc_type:
            continue
        if year_from and (rec.get("year") or 0) < year_from:
            continue
        if year_to and (rec.get("year") or 9999) > year_to:
            continue

        blob = _blob(rec)
        score = 0.0
        for term in terms:
            for field, weight in WEIGHTS.items():
                if term in blob[field]:
                    score += weight
        if not terms:  # 无关键词时按新近度和实用度排序
            score = 1.0
        if score <= 0:
            continue
        score += (rec.get("relevance") or 3) * 0.8
        score += (rec.get("year") or 2000 - 2000) / 100.0
        if rec.get("status") == "reviewed":
            score += 1.0
        scored.append((score, rec))

    scored.sort(key=lambda x: (-x[0], -(x[1].get("year") or 0)))
    return [r for _, r in scored[: max(1, min(limit, 50))]]


def fmt_record(rec: dict, with_notes: bool = False) -> str:
    authors = rec.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    author_str = ", ".join(authors[:3]) + (" 等" if len(authors) > 3 else "")
    lines = [
        f"【{rec.get('id')}】{rec.get('title')}",
        f"  作者：{author_str or '—'}",
        f"  来源：{rec.get('journal') or '—'} {rec.get('year') or ''}"
        f"{'  ' + str(rec.get('volume')) if rec.get('volume') else ''}"
        f"{':' + str(rec.get('pages')) if rec.get('pages') else ''}",
        f"  类型：{rec.get('type') or '—'} ｜ 主题：{'、'.join(rec.get('topics') or []) or '—'}"
        f" ｜ 证据等级：{rec.get('evidence_level') or '—'}",
    ]
    if rec.get("doi"):
        lines.append(f"  DOI：{rec['doi']}  https://doi.org/{rec['doi']}")
    elif rec.get("pmid"):
        lines.append(f"  PMID：{rec['pmid']}  https://pubmed.ncbi.nlm.nih.gov/{rec['pmid']}/")
    if rec.get("abstract"):
        lines.append(f"  摘要：{str(rec['abstract'])[:600]}"
                     + ("…" if len(str(rec["abstract"])) > 600 else ""))
    if with_notes:
        notes = str(rec.get("_notes") or "").strip()
        if notes:
            lines.append("  笔记：")
            lines += [f"    {ln}" for ln in notes.splitlines() if ln.strip()][:60]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 工具定义
# --------------------------------------------------------------------------
TOOLS = [
    {
        "name": "search_literature",
        "description": (
            "在麻醉科文献库中检索。支持中文或英文关键词，可限定主题、文献类型和年份。"
            "返回匹配文献的题录、摘要片段与 DOI/PMID。当用户询问某个麻醉相关临床问题的"
            "证据、指南或研究时使用本工具。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词，中文或英文，如“术后谵妄”或“regional anesthesia”"},
                "topic": {"type": "string", "description": "限定主题（须为库中已定义的主题，如“术后镇痛”）"},
                "type": {"type": "string", "description": "文献类型：RCT / Meta-analysis / Guideline / Review / Observational 等"},
                "year_from": {"type": "integer", "description": "起始年份（含）"},
                "year_to": {"type": "integer", "description": "截止年份（含）"},
                "limit": {"type": "integer", "description": "返回条数，默认 8，最大 50"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_paper",
        "description": "按 id 取出某篇文献的完整元数据与人工笔记全文。id 形如 2022-li-regional-general-anesthesia。",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "文献 id（即目录名）"}},
            "required": ["id"],
        },
    },
    {
        "name": "list_topics",
        "description": "列出文献库的主题分类树，以及每个叶子主题下已有的文献数量。用于了解库的覆盖范围、发现空白领域。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "library_stats",
        "description": "文献库总体统计：总数、按类型、按年份、按状态分布。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "find_related",
        "description": "给定一篇文献的 id，找出主题重叠度最高的其他文献。用于快速梳理同一主题的证据脉络。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "基准文献 id"},
                "limit": {"type": "integer", "description": "返回条数，默认 5"},
            },
            "required": ["id"],
        },
    },
]


def call_tool(name: str, args: dict) -> tuple[str, bool]:
    if name == "search_literature":
        query = str(args.get("query") or "").strip()
        if not query:
            return "参数 query 不能为空。", True
        rows = search(
            query,
            topic=args.get("topic"),
            doc_type=args.get("type"),
            year_from=args.get("year_from"),
            year_to=args.get("year_to"),
            limit=int(args.get("limit") or 8),
        )
        if not rows:
            return (f"库中没有匹配“{query}”的文献。\n"
                    "建议：换用更宽泛的关键词，或先运行 python scripts/fetch_metadata.py search \"…\" 从 PubMed 补充入库。"), False
        head = f"命中 {len(rows)} 篇（共 {len(LIB.records())} 篇在库）：\n\n"
        return head + "\n\n".join(fmt_record(r) for r in rows), False

    if name == "get_paper":
        rec = LIB.by_id(str(args.get("id") or ""))
        if not rec:
            return f"未找到 id = {args.get('id')!r} 的文献。可用 search_literature 先检索。", True
        return fmt_record(rec, with_notes=True), False

    if name == "list_topics":
        tree = topic_tree()
        counts: dict[str, int] = {}
        for rec in LIB.records():
            for t in rec.get("topics") or []:
                counts[t] = counts.get(t, 0) + 1
        lines = ["主题分类树（括号内为在库文献数）："]
        for cat, subs in tree.items():
            lines.append(f"\n▸ {cat}")
            if isinstance(subs, list):
                for s in subs:
                    lines.append(f"    {s}（{counts.get(s, 0)}）")
        total = sum(counts.values())
        lines.append(f"\n叶子主题合计标注 {total} 次，覆盖 {len([c for c in counts.values() if c])} 个主题。")
        lines.append("未在库中出现的主题说明尚有空白，可作为后续检索方向。")
        return "\n".join(lines), False

    if name == "library_stats":
        recs = LIB.records()
        by_type: dict[str, int] = {}
        by_year: dict[int, int] = {}
        by_status: dict[str, int] = {}
        for r in recs:
            by_type[r.get("type") or "—"] = by_type.get(r.get("type") or "—", 0) + 1
            y = r.get("year")
            if isinstance(y, int):
                by_year[y] = by_year.get(y, 0) + 1
            by_status[r.get("status") or "—"] = by_status.get(r.get("status") or "—", 0) + 1
        lines = [f"文献库共 {len(recs)} 篇", "", "按类型："]
        lines += [f"  {k}: {v}" for k, v in sorted(by_type.items(), key=lambda x: -x[1])]
        lines.append("")
        lines.append("按年份（近 8 年）：")
        for y in sorted(by_year, reverse=True)[:8]:
            lines.append(f"  {y}: {by_year[y]}")
        lines.append("")
        lines.append("按状态：")
        lines += [f"  {k}: {v}" for k, v in sorted(by_status.items(), key=lambda x: -x[1])]
        return "\n".join(lines), False

    if name == "find_related":
        base = LIB.by_id(str(args.get("id") or ""))
        if not base:
            return f"未找到 id = {args.get('id')!r} 的文献。", True
        base_topics = set(base.get("topics") or [])
        base_tags = set(base.get("tags") or [])
        scored = []
        for rec in LIB.records():
            if rec["id"] == base["id"]:
                continue
            overlap = len(base_topics & set(rec.get("topics") or []))
            tag_overlap = len(base_tags & set(rec.get("tags") or []))
            if overlap or tag_overlap:
                scored.append((overlap * 3 + tag_overlap, rec))
        scored.sort(key=lambda x: (-x[0], -(x[1].get("year") or 0)))
        top = scored[: max(1, min(int(args.get("limit") or 5), 20))]
        if not top:
            return f"库中没有与《{base.get('title')}》主题重叠的其他文献。", False
        return (f"与《{base.get('title')}》主题相近的文献：\n\n"
                + "\n\n".join(fmt_record(r) for _, r in top)), False

    return f"未知工具：{name}", True


# --------------------------------------------------------------------------
# JSON-RPC / MCP 循环
# --------------------------------------------------------------------------
def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(req: dict) -> dict | None:
    method = req.get("method")
    rid = req.get("id")

    if method == "initialize":
        client_proto = (req.get("params") or {}).get("protocolVersion")
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {
                "protocolVersion": client_proto or DEFAULT_PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            text, is_error = call_tool(name, args)
        except Exception as exc:  # noqa: BLE001
            text, is_error = f"工具执行失败：{exc}\n{traceback.format_exc()}", True
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
        }

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"resources": []}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"prompts": []}}

    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


def serve() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            send({"jsonrpc": "2.0", "id": None,
                  "error": {"code": -32700, "message": f"Parse error: {exc}"}})
            continue
        if isinstance(req, list):
            for item in req:
                resp = handle(item)
                if resp is not None:
                    send(resp)
        else:
            resp = handle(req)
            if resp is not None:
                send(resp)
    return 0


def selftest() -> int:
    print(f"库路径：{ROOT}")
    print(f"文献数：{len(LIB.records())}\n")
    for name, args in [
        ("library_stats", {}),
        ("search_literature", {"query": "术后谵妄", "limit": 3}),
        ("search_literature", {"query": "regional anesthesia", "limit": 3}),
        ("list_topics", {}),
    ]:
        print(f"--- {name} {args} ---")
        text, err = call_tool(name, args)
        print(text[:1200])
        print(f"[isError={err}]\n")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    raise SystemExit(serve())
