#!/usr/bin/env python3
"""把 digests/*.md 轉成 data/news.json，供網站前端使用。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "digests"
DEFAULT_OUTPUT = ROOT / "data" / "news.json"

LABEL_TO_CATEGORY = {
    "🔥 頭條": "headline",
    "🔬 研究": "research",
    "🛠️ 工具": "tools",
    "💭 觀點": "opinion",
}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def parse_meta_line(line: str) -> dict:
    meta = {
        "source": None,
        "score": None,
        "comments": None,
        "readingTime": None,
        "hnUrl": None,
        "summarized": True,
    }
    m = re.search(r"`([^`]+)`", line)
    if m:
        meta["source"] = m.group(1).strip()
    for token in re.split(r"\s*·\s*", line):
        token = token.strip()
        if not token:
            continue
        if "⬆️" in token:
            mm = re.search(r"(\d+)", token)
            if mm:
                meta["score"] = int(mm.group(1))
        elif "💬" in token:
            mm = re.search(r"(\d+)", token)
            if mm:
                meta["comments"] = int(mm.group(1))
        elif "📖" in token:
            mm = re.search(r"(\d+)", token)
            if mm:
                meta["readingTime"] = int(mm.group(1))
        elif "HN 討論" in token:
            mm = re.search(r"\(([^)]+)\)", token)
            if mm:
                meta["hnUrl"] = mm.group(1).strip()
        elif "⚠️" in token:
            meta["summarized"] = False
            if "擷取式" in token:
                meta["summaryMode"] = "extractive"
            elif "來源簡介" in token:
                meta["summaryMode"] = "blurb"
            else:
                meta["summaryMode"] = ""
    return meta


def parse_item(header: str, block: list[str]) -> dict | None:
    m = re.match(r"^(\d+)\.\s*\[(.+?)\]\((.+?)\)\s*$", header.strip())
    if not m:
        return None
    rank = int(m.group(1))
    title = m.group(2).strip()
    url = m.group(3).strip()

    tldr = None
    points: list[str] = []
    why = None
    meta_line = None

    for raw in block:
        s = raw.strip()
        if not s:
            continue
        if s.startswith("**TLDR：**") or s.startswith("**TLDR:**"):
            tldr = s.split("**", 2)[-1]
            # remove leading "TLDR："/"TLDR:" if present
            tldr = re.sub(r"^TLDR[：:]\s*", "", tldr.strip())
        elif s.startswith("- "):
            points.append(s[2:].strip())
        elif s.startswith("> 💡"):
            why = s[len("> 💡"):].strip()
        elif "`" in s or "·" in s:
            meta_line = s

    item = {
        "rank": rank,
        "title": title,
        "url": url,
        "tldr": tldr,
        "points": points,
        "why": why,
        "meta": parse_meta_line(meta_line) if meta_line else {
            "source": None, "score": None, "comments": None,
            "readingTime": None, "hnUrl": None, "summarized": True,
        },
    }
    return item


def parse_digest(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[warn] 讀取失敗 {path}: {e}", file=sys.stderr)
        return None

    meta, body = parse_frontmatter(text)
    date = meta.get("date") or path.stem
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        date = path.stem

    title = None
    sections: list[dict] = []
    current_section: dict | None = None
    current_item_header: str | None = None
    current_block: list[str] = []

    def finalize_item():
        nonlocal current_item_header, current_block, current_section
        if current_item_header is None:
            return
        item = parse_item(current_item_header, current_block)
        if item is not None:
            if current_section is None:
                current_section = {"id": "other", "label": "其他", "items": []}
                sections.append(current_section)
            item["category"] = current_section["id"]
            current_section["items"].append(item)
        current_item_header = None
        current_block = []

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and title is None:
            title = stripped[2:].strip()
            continue
        if stripped.startswith("## "):
            finalize_item()
            label = stripped[3:].strip()
            cat = LABEL_TO_CATEGORY.get(label, label)
            current_section = {"id": cat, "label": label, "items": []}
            sections.append(current_section)
            continue
        if stripped.startswith("### "):
            finalize_item()
            current_item_header = stripped[4:].strip()
            current_block = []
            continue
        if current_item_header is not None:
            current_block.append(line)
            continue
        # ignore other lines

    finalize_item()

    # drop empty sections
    sections = [s for s in sections if s["items"]]
    if not sections:
        print(f"[warn] {path.name} 沒有解析到任何新聞，跳過", file=sys.stderr)
        return None

    item_count = sum(len(s["items"]) for s in sections)
    return {
        "date": date,
        "title": title or f"AI 每日摘要 · {date}",
        "sections": sections,
        "itemCount": item_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="將 digests/*.md 建置為 data/news.json")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="digests 目錄")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="輸出 JSON 路徑")
    args = parser.parse_args()

    source = args.source
    if not source.is_absolute():
        source = (Path.cwd() / source).resolve()
    output = args.output
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()

    if not source.exists():
        print(f"[error] 找不到來源目錄: {source}", file=sys.stderr)
        return 1

    files = sorted(source.glob("????-??-??.md"), reverse=True)
    days = []
    seen = set()
    for f in files:
        day = parse_digest(f)
        if day is None:
            continue
        if day["date"] in seen:
            continue
        seen.add(day["date"])
        days.append(day)

    days.sort(key=lambda d: d["date"], reverse=True)

    payload = {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "days": days,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已建置 {len(days)} 天、{sum(d['itemCount'] for d in days)} 則 → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
