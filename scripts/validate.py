#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麻醉科文献库 — 校验器

做两件事：
  1) 结构校验：每条 meta.yaml 的必填字段、枚举值、主题是否合法、目录名与 id 是否一致。
  2) 隐私体检：扫描 meta.yaml / notes.md，发现疑似患者隐私或未发表数据的内容并报警。

用法：
    python scripts/validate.py            # 全部检查
    python scripts/validate.py --strict   # 把警告也当失败（提交前用）
退出码：0 通过 / 1 有问题
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("缺少 PyYAML：python -m pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent
PAPERS_DIR = ROOT / "papers"
TOPICS_FILE = ROOT / "schema" / "topics.yaml"

REQUIRED_FIELDS = ["id", "title", "authors", "year", "type", "topics", "status"]
ENUMS = {
    "type": {"RCT", "Meta-analysis", "Systematic review", "Guideline", "Review",
             "Observational", "Case report", "Editorial", "Basic science", "Other"},
    "evidence_level": {"high", "moderate", "low", "very-low", "na"},
    "oa_status": {"open", "closed", "unknown"},
    "status": {"draft", "reviewed", "archived"},
}
ID_PATTERN = re.compile(r"^[0-9]{4}-[a-z0-9]+(-[a-z0-9]+)*$")
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

# --------------------------------------------------------------------------
# 隐私体检规则：命中即报警（不是绝对判断，需人工复核）
# --------------------------------------------------------------------------
PRIVACY_RULES: list[tuple[str, str, re.Pattern]] = [
    ("critical", "疑似患者身份编号", re.compile(
        r"(住院号|病案号|门诊号|床号|登记号|就诊号|身份证|医保卡号|社保号|MRN\b|病历号)")),
    ("critical", "疑似身份证号", re.compile(r"\b[1-9]\d{5}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b")),
    ("critical", "疑似手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("critical", "疑似患者姓名标注", re.compile(r"(患者|病人|患儿)\s*[:：]?\s*[\u4e00-\u9fa5]{2,4}(先生|女士|同志|同学)")),
    ("warn", "疑似未发表数据", re.compile(
        r"(未发表|审稿意见|投稿中|在投|基金申请|标书|伦理审批|知情同意书|原始数据|随访表)")),
    ("warn", "疑似影像/监护截图", re.compile(r"(\.dcm\b|DICOM|监护截图|超声图像截图|心电图原图)")),
    ("warn", "疑似联系方式", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")),
]

# 模板自带的免责声明行，不参与隐私扫描
SAFE_LINE = re.compile(r"(严禁|禁止|不得写入|不要写入|勿写入|不得包含)")


def load_topic_leaves() -> set[str]:
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


def lint_privacy(text: str, where: str) -> list[tuple[str, str, str]]:
    """逐行扫描；跳过「严禁写入患者信息」这类模板免责声明行，避免自我误报。"""
    findings = []
    lines = text.splitlines()
    for severity, label, pattern in PRIVACY_RULES:
        for line in lines:
            if SAFE_LINE.search(line):
                continue
            m = pattern.search(line)
            if m:
                snippet = line[max(0, m.start() - 25): m.end() + 25]
                findings.append((severity, label, f"{where}: …{snippet}…"))
                break  # 同一规则每文件只报一次，避免刷屏
    return findings


def validate_paper(path: Path, leaves: set[str]) -> tuple[list[str], list[str], list[tuple]]:
    errors: list[str] = []
    warnings: list[str] = []
    privacy: list[tuple] = []

    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return [f"{path}: 无法读取 ({exc})"], [], []

    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return [f"{path}: YAML 语法错误 → {exc}"], [], []

    if not isinstance(meta, dict):
        return [f"{path}: 内容不是键值对结构"], [], []

    for field in REQUIRED_FIELDS:
        if field not in meta or meta[field] in (None, "", []):
            errors.append(f"{path}: 缺少必填字段 `{field}`")

    pid = str(meta.get("id") or "")
    if pid and not ID_PATTERN.match(pid):
        errors.append(f"{path}: id 格式不合法 → {pid}")
    if pid and pid != path.parent.name:
        errors.append(f"{path}: id ({pid}) 与目录名 ({path.parent.name}) 不一致")

    for field, allowed in ENUMS.items():
        val = meta.get(field)
        if val is not None and val not in allowed:
            errors.append(f"{path}: `{field}` = {val!r} 不在允许值 {sorted(allowed)} 中")

    year = meta.get("year")
    if year is not None and not isinstance(year, int):
        errors.append(f"{path}: `year` 必须是整数，当前为 {type(year).__name__}")

    authors = meta.get("authors")
    if authors is not None and not isinstance(authors, list):
        errors.append(f"{path}: `authors` 必须是列表")

    topics = meta.get("topics") or []
    if not isinstance(topics, list):
        errors.append(f"{path}: `topics` 必须是列表")
    else:
        for t in topics:
            if t not in leaves:
                errors.append(f"{path}: 主题 `{t}` 不在 schema/topics.yaml 的叶子主题中")

    for field in ("added_date", "updated_date"):
        val = meta.get(field)
        if val and not DATE_PATTERN.match(str(val)):
            errors.append(f"{path}: `{field}` 应为 YYYY-MM-DD，当前为 {val!r}")

    relevance = meta.get("relevance")
    if relevance is not None and (not isinstance(relevance, int) or not 1 <= relevance <= 5):
        warnings.append(f"{path}: `relevance` 建议为 1-5 的整数，当前为 {relevance!r}")

    if not meta.get("doi") and not meta.get("pmid"):
        warnings.append(f"{path}: 既无 DOI 也无 PMID，后续很难追溯")
    if meta.get("status") == "draft":
        warnings.append(f"{path}: 状态仍是 draft（待人工核对）")

    privacy += lint_privacy(raw, str(path.relative_to(ROOT)))
    notes = path.parent / "notes.md"
    if notes.exists():
        privacy += lint_privacy(notes.read_text(encoding="utf-8"), str(notes.relative_to(ROOT)))
    else:
        warnings.append(f"{path.parent}: 缺少 notes.md")

    return errors, warnings, privacy


def main() -> int:
    ap = argparse.ArgumentParser(description="校验文献库")
    ap.add_argument("--strict", action="store_true", help="警告也视为失败")
    args = ap.parse_args()

    if not PAPERS_DIR.exists():
        print("papers/ 目录不存在，无内容可校验。")
        return 0

    leaves = load_topic_leaves()
    all_errors: list[str] = []
    all_warnings: list[str] = []
    all_privacy: list[tuple] = []
    count = 0

    for meta_path in sorted(PAPERS_DIR.glob("*/meta.yaml")):
        count += 1
        e, w, p = validate_paper(meta_path, leaves)
        all_errors += e
        all_warnings += w
        all_privacy += p

    print(f"共检查 {count} 篇文献\n")

    critical = [x for x in all_privacy if x[0] == "critical"]
    warned = [x for x in all_privacy if x[0] == "warn"]

    if critical:
        print("!! 隐私红线告警（必须处理，禁止提交）")
        for _, label, detail in critical:
            print(f"   [严重] {label}\n          {detail}")
        print()
    if warned:
        print("!  需人工复核（可能是未发表数据或联系方式）")
        for _, label, detail in warned:
            print(f"   [复核] {label}\n          {detail}")
        print()

    if all_errors:
        print("结构错误：")
        for e in all_errors:
            print(f"   - {e}")
        print()
    if all_warnings:
        print("提示：")
        for w in all_warnings:
            print(f"   - {w}")
        print()

    failed = bool(all_errors) or bool(critical) or (args.strict and bool(all_warnings))
    if failed:
        print("结果：未通过")
        return 1
    print("结果：通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
