"""Anthropic 官方 news：沒有 RSS，直接解析 SSR 的 news 頁面。"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests

from ..config import FETCH_TIMEOUT, FETCH_UA
from ..models import Candidate

NEWS_URL = "https://www.anthropic.com/news"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ANCHOR_RE = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_TITLE_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
_SUMMARY_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_TIME_RE = re.compile(r"<time[^>]*>(.*?)</time>", re.S)
_DATE_RE = re.compile(r"^[A-Za-z]{3,9}\.?\s+\d{1,2},\s+\d{4}$")


def _clean(raw: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub("", raw or ""))).strip()


def _parse_date(raw: str) -> int:
    text = _clean(raw).replace("Sept", "Sep")
    if not _DATE_RE.match(text):
        return 0
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return 0


def fetch_anthropic() -> list[Candidate]:
    try:
        res = requests.get(NEWS_URL, headers={"User-Agent": FETCH_UA}, timeout=FETCH_TIMEOUT)
        res.raise_for_status()
        page = res.text or ""
    except Exception:
        return []

    out: list[Candidate] = []
    seen: dict[str, int] = {}  # url -> index in out
    for m in _ANCHOR_RE.finditer(page):
        href, chunk = m.group(1), m.group(2)
        if not re.match(r"^/(news|research|features|claude)", href):
            continue
        title_m = _TITLE_RE.search(chunk)
        title = _clean(title_m.group(1)) if title_m else ""
        if not title:
            continue

        # 日期在卡片 header 的 <time> 裡；太遠就當沒有日期，避免抓到上一張卡片的日期。
        published_at = 0
        before = page[: m.start()]
        times = list(_TIME_RE.finditer(before))
        if times and m.start() - times[-1].end() < 4000:
            published_at = _parse_date(times[-1].group(1))

        summary_m = _SUMMARY_RE.search(chunk)
        blurb = _clean(summary_m.group(1))[:300] if summary_m else ""
        url = urljoin("https://www.anthropic.com", href)

        if url in seen:
            # 同一個卡片常有 desktop / mobile 兩份；用有日期的版本補資料。
            if published_at and not out[seen[url]].published_at:
                out[seen[url]].published_at = published_at
            continue

        seen[url] = len(out)
        out.append(
            Candidate(
                source_id="anthropic",
                source_label="Anthropic",
                title=title,
                url=url,
                published_at=published_at,
                blurb=blurb,
            )
        )
    return out
