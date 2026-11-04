"""輸出層：Obsidian-friendly Markdown + digests/index.md 索引。"""

from __future__ import annotations

from pathlib import Path

from .config import CATEGORY_LABELS, DIGESTS_DIR


def digest_path(date: str) -> Path:
    return DIGESTS_DIR / f"{date}.md"


def _meta_line(it: dict) -> str:
    parts: list[str] = [f"`{it['source_label']}`"]
    if it.get("score") is not None:
        parts.append(f"⬆️ {it['score']}")
    if it.get("comments") is not None:
        parts.append(f"💬 {it['comments']}")
    if it.get("reading_time"):
        parts.append(f"📖 {it['reading_time']} 分鐘")
    if it.get("hn_url"):
        parts.append(f"[HN 討論]({it['hn_url']})")
    if not it.get("summarized", True):
        parts.append("⚠️ 未摘要（LLM 失敗，僅顯示來源簡介）")
    return " · ".join(str(p) for p in parts)


def render_markdown(date: str, items: list[dict]) -> str:
    lines = [
        "---",
        f"date: {date}",
        "type: ai-news-digest",
        "tags: [ai-news, daily]",
        "---",
        "",
        f"# 🗞️ AI 每日摘要 · {date}",
        "",
    ]
    current_cat: str | None = None
    for it in items:
        if it["category"] != current_cat:
            current_cat = it["category"]
            lines += ["---", "", f"## {CATEGORY_LABELS.get(current_cat, current_cat)}", ""]
        lines.append(f"### {it['rank']}. [{it['title']}]({it['url']})")
        lines.append("")
        if it.get("tldr"):
            lines.append(f"**TLDR：** {it['tldr']}")
            lines.append("")
        for p in it.get("points") or []:
            lines.append(f"- {p}")
        if it.get("points"):
            lines.append("")
        if it.get("why"):
            lines.append(f"> 💡 {it['why']}")
            lines.append("")
        lines.append(_meta_line(it))
        lines.append("")
    lines += ["---", "", "*由 [ai-news](https://github.com/jackchen890311/AI-News) 自動收集與摘要*"]
    return "\n".join(lines) + "\n"


def write_index() -> None:
    """重建 digests/index.md：所有日期的目錄。"""
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(DIGESTS_DIR.glob("????-??-??.md"), reverse=True)
    lines = ["# 🗞️ AI 每日摘要 · 索引", ""]
    if files:
        lines += ["| 日期 | 摘要 |", "| --- | --- |"]
        for f in files:
            lines.append(f"| {f.stem} | [{f.stem}]({f.name}) |")
    else:
        lines.append("*還沒有任何摘要。*")
    (DIGESTS_DIR / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
