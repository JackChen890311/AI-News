"""ai-news CLI。

執行方式（在 repo 根目錄）：uv run python -m ai_news <command>

指令：
    digest     產生今日摘要（--force 重抓 / --limit N 測試 / --no-summarize 只抓清單）
    open       開啟今日（或最新一份）摘要
    show       顯示某日摘要
    search     搜尋歷史摘要
    install    安裝 launchd 每日排程（macOS）
    uninstall  移除排程
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .config import (
    CACHE_MIN_ITEMS,
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    CONCURRENCY,
    DB_PATH,
    DEDUP_DAYS,
    DIGESTS_DIR,
    MIN_CONTENT_LENGTH,
    REPO_ROOT,
    SCHEDULE_HOUR,
    SCHEDULE_LABEL,
    SCHEDULE_MINUTE,
    DATA_DIR,
)
from .fetch import collect_all
from .fetch.extract import fetch_article_text
from .fetch.hn import fetch_hn_comments
from .models import Candidate
from .rank import pick
from .render import digest_path, render_markdown, write_index
from .store import Store
from .summarize import Summary, reading_time, summarize_article


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------- digest


def _process_one(
    date: str, category: str, rank: int, c: Candidate, no_summarize: bool, store: Store
) -> dict | None:
    """單則流程：抓正文 → 摘要 → 存 DB。任何一步失敗都有備援，不讓整份 digest 開天窗。"""
    text: str | None = None
    pdf_url: str | None = None
    summarized = False

    if not no_summarize:
        text, pdf_url = fetch_article_text(c.url)
        # 正文抓不到時，HN 熱門留言當最後備援（跟 hn-ai-digest 同概念）
        if not text and c.source_id == "hn" and c.hn_id and (c.comments or 0) >= 20:
            comments = fetch_hn_comments(c.hn_id)
            if comments and len(comments) >= MIN_CONTENT_LENGTH:
                text = "以下是這篇新聞的 Hacker News 熱門留言，供理解討論重點：\n\n" + comments

    rt = reading_time(text) if text else 0

    if no_summarize:
        summary = Summary(ok=False, tldr=(c.blurb or "")[:200])
    elif pdf_url:
        # PDF 沒法抽正文，用 blurb 佔位
        summary = Summary(ok=bool(c.blurb), tldr=(c.blurb or "")[:200] or f"[PDF] {c.url}")
    elif not text:
        summary = Summary(ok=bool(c.blurb), tldr=(c.blurb or "")[:200])
        if not summary.tldr:
            _log(f"  ⏭️  [{c.source_label}] 抓不到內容也沒有簡介，跳過：{c.title[:50]}")
            return None
    else:
        summary = summarize_article(c.title, text)
        summarized = summary.ok
        if not summarized:
            reason = (summary.raw or "").strip().replace("\n", " ")[:140]
            summary.tldr = summary.tldr or (c.blurb or "")[:200]
            _log(f"  ⚠️  [{c.source_label}] 摘要失敗（{reason or '回覆格式解析失敗'}）；用簡介墊：{c.title[:40]}")
        else:
            _log(f"  ✅ [{c.source_label}] {c.title[:60]}")

    store.save_item(
        date, category, rank, c, summary.tldr, summary.points, summary.why,
        rt if summarized else 0, summarized,
    )
    return {
        "category": category,
        "rank": rank,
        "title": c.title,
        "url": c.url,
        "source_id": c.source_id,
        "source_label": c.source_label,
        "tldr": summary.tldr,
        "points": summary.points,
        "why": summary.why,
        "score": c.score,
        "comments": c.comments,
        "reading_time": rt if summarized else 0,
        "hn_url": c.hn_url,
        "summarized": summarized,
    }


def _print_digest(items: list[dict], date: str, cached: bool) -> None:
    try:
        from rich.console import Console
    except ImportError:
        for it in items:
            print(f"[{CATEGORY_LABELS.get(it['category'], it['category'])}] "
                  f"{it['rank']}. {it['title']}\n    {it.get('tldr', '')}")
        return

    console = Console()
    console.rule(f"🗞️  AI 每日摘要 · {date}" + ("（快取）" if cached else ""))
    cur = None
    for it in items:
        if it["category"] != cur:
            cur = it["category"]
            console.print(f"\n[bold]{CATEGORY_LABELS.get(cur, cur)}[/bold]")
        console.print(f"  [bold cyan]{it['rank']}.[/bold cyan] [link={it['url']}]{it['title']}[/link]")
        if it.get("tldr"):
            console.print(f"     {it['tldr']}")
        meta = []
        if it.get("score") is not None:
            meta.append(f"⬆️ {it['score']}")
        if it.get("comments") is not None:
            meta.append(f"💬 {it['comments']}")
        if it.get("reading_time"):
            meta.append(f"📖 {it['reading_time']} 分鐘")
        if not it.get("summarized", True):
            meta.append("⚠️ 未摘要")
        if meta:
            console.print(f"     [dim]{' · '.join(meta)}[/dim]")


def cmd_digest(args) -> int:
    date = datetime.now().astimezone().date().isoformat()
    store = Store(DB_PATH)

    # CI 每次都是全新的 DB；從已入版控的 digests/ 回填去重 key，
    # 才不會因為沒有 data/state.db 而每天重複選到同一批新聞。
    try:
        added = store.backfill_from_digests(DIGESTS_DIR)
        if added:
            _log(f"♻️  從歷史 digests 回填 {added} 個去重 key")
    except Exception as e:  # 回填失敗不該擋住今天的 digest
        _log(f"（回填去重 key 失敗，忽略：{e}）")

    cached = False

    if not args.force and store.count_today(date) >= CACHE_MIN_ITEMS:
        items = store.items_for(date)
        for it in items:
            try:
                it["points"] = json.loads(it["points"] or "[]")
            except json.JSONDecodeError:
                it["points"] = []
            it["summarized"] = bool(it.get("summarized"))
        cached = True
        _log(f"✅ {date} 已有 {len(items)} 則快取（--force 可重抓）")
    else:
        if args.force:
            store.replace_day(date)
        exclude = store.recent_keys(DEDUP_DAYS)
        now = int(time.time())

        _log(f"🔍 收集候選中（去重比對最近 {DEDUP_DAYS} 天，{len(exclude)} 個已知 key）…")
        cands = collect_all()
        _log(f"📰 {len(cands)} 個候選")

        picked, pool = pick(cands, exclude, now)
        from collections import Counter

        _log(f"📊 候選分類統計：{dict(Counter(c.category for c in pool))}")
        flat = [(cat, i + 1, c) for cat in CATEGORY_ORDER for i, c in enumerate(picked[cat])]
        if args.limit:
            flat = flat[: args.limit]
        if not flat:
            _log("❌ 今天沒有選出任何新聞（全部被去重或不符合 AI 關鍵字）")
            return 1
        _log(f"✂️  選出 {len(flat)} 則，開始抓正文與摘要（平行 {CONCURRENCY}）…")

        items = []
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            futures = [
                ex.submit(_process_one, date, cat, rank, c, args.no_summarize, store)
                for cat, rank, c in flat
            ]
            for fut in as_completed(futures):
                try:
                    item = fut.result()
                except Exception as e:
                    _log(f"  ❌ 單則處理失敗：{e}")
                    continue
                if item:
                    items.append(item)

        if not items:
            _log("❌ 沒有任何一則成功處理")
            return 1
        order = {cat: i for i, cat in enumerate(CATEGORY_ORDER)}
        items.sort(key=lambda it: (order[it["category"]], it["rank"]))

    md = render_markdown(date, items)
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    path = digest_path(date)
    path.write_text(md, encoding="utf-8")
    write_index()

    _print_digest(items, date, cached)
    try:
        from rich.console import Console

        Console().print(f"\n✅ 已寫入 [bold]{path}[/bold]\n   開啟：[bold]uv run python -m ai_news open[/bold]")
    except ImportError:
        print(f"\n✅ 已寫入 {path}")

    if not cached and any(not it.get("summarized") for it in items):
        _log("（有些則未成功摘要，跑 --force 可重試）")
    return 0


# ---------------------------------------------------------------- open / show / search


def _latest_digest() -> Path | None:
    files = sorted(DIGESTS_DIR.glob("????-??-??.md"))
    return files[-1] if files else None


def cmd_open(_args) -> int:
    path = digest_path(datetime.now().astimezone().date().isoformat())
    if not path.exists():
        path = _latest_digest()
    if not path:
        print("還沒有任何摘要，先跑：uv run python -m ai_news digest")
        return 1

    opener = "open" if platform.system().lower() == "darwin" else "xdg-open"
    if os.environ.get("AI_NEWS_OBSIDIAN") == "1":
        import urllib.parse

        subprocess.run([opener, "obsidian://open?path=" + urllib.parse.quote(str(path.resolve()))])
    else:
        subprocess.run([opener, str(path)])
    print(f"已開啟 {path}")
    return 0


def cmd_show(args) -> int:
    date = args.date or datetime.now().astimezone().date().isoformat()
    path = DIGESTS_DIR / f"{date}.md"
    if not path.exists():
        print(f"找不到 {path}")
        latest = _latest_digest()
        if latest:
            print(f"最新一份是：{latest}")
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_search(args) -> int:
    store = Store(DB_PATH)
    rows = store.search(args.query)
    if not rows:
        print(f"（查無「{args.query}」）")
        return 0
    for r in rows:
        print(f"{r['date']}  [{CATEGORY_LABELS.get(r['category'], r['category'])}] {r['title']}")
        if r.get("tldr"):
            print(f"           {r['tldr'][:100]}")
        print(f"           {r['url']}")
    return 0


# ---------------------------------------------------------------- install / uninstall（launchd）


_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{uv_path}</string>
        <string>run</string>
        <string>--project</string>
        <string>{repo}</string>
        <string>python</string>
        <string>-m</string>
        <string>ai_news</string>
        <string>digest</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{path_env}</string>
        <key>HOME</key>
        <string>{home}</string>
    </dict>
</dict>
</plist>
"""


def cmd_install(_args) -> int:
    if platform.system().lower() != "darwin":
        print("install 只支援 macOS launchd；其他平台請用 crontab / systemd timer 呼叫同一個指令。")
        return 1

    uv_path = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if not Path(uv_path).exists():
        print(f"找不到 uv（{uv_path}），請先安裝 uv。")
        return 1

    extra = [
        str(Path.home() / ".opencode" / "bin"),
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
    ]
    parts = [p for p in os.environ.get("PATH", "").split(":") if p] + extra
    path_env = ":".join(dict.fromkeys(parts))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_path = DATA_DIR / "launchd.log"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{SCHEDULE_LABEL}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    plist_path.write_text(
        _PLIST_TEMPLATE.format(
            label=SCHEDULE_LABEL,
            uv_path=uv_path,
            repo=str(REPO_ROOT),
            hour=SCHEDULE_HOUR,
            minute=SCHEDULE_MINUTE,
            log_path=log_path,
            path_env=path_env,
            home=str(Path.home()),
        ),
        encoding="utf-8",
    )
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    r = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"launchctl load 失敗：{r.stderr.strip()}")
        print(f"plist 在 {plist_path}，可手動執行：launchctl load {plist_path}")
        return 1

    print(f"✅ 排程已安裝：每天 {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} 自動執行 ai-news digest")
    print(f"   plist：{plist_path}")
    print(f"   log：{log_path}")
    print(f"   移除：uv run python -m ai_news uninstall")
    return 0


def cmd_uninstall(_args) -> int:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{SCHEDULE_LABEL}.plist"
    if not plist_path.exists():
        print(f"沒有找到 {plist_path}，不需移除。")
        return 0
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    plist_path.unlink()
    print(f"✅ 已移除排程（{SCHEDULE_LABEL}）")
    return 0


# ---------------------------------------------------------------- main


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ai-news", description="每日 AI 新聞摘要：多來源收集、排序、去重、繁中摘要"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_digest = sub.add_parser("digest", help="產生今日摘要（Markdown）")
    p_digest.add_argument("--force", action="store_true", help="忽略今日快取，重新收集")
    p_digest.add_argument("--limit", type=int, default=0, help="只處理前 N 則（測試用）")
    p_digest.add_argument("--no-summarize", action="store_true", help="不呼叫 LLM，只抓清單（測試用）")

    sub.add_parser("open", help="在瀏覽器 / Obsidian 開啟今日摘要")
    p_show = sub.add_parser("show", help="顯示某日摘要（預設今天）")
    p_show.add_argument("date", nargs="?", default=None, help="YYYY-MM-DD")

    p_search = sub.add_parser("search", help="搜尋歷史摘要")
    p_search.add_argument("query")

    sub.add_parser("install", help="安裝 launchd 每日排程（macOS）")
    sub.add_parser("uninstall", help="移除 launchd 排程")

    args = parser.parse_args(argv)
    handlers = {
        "digest": cmd_digest,
        "open": cmd_open,
        "show": cmd_show,
        "search": cmd_search,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
