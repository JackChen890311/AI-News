"""一般 RSS / Atom 來源（feedparser 同時支援兩種格式）。"""

from __future__ import annotations

import calendar
import html

import feedparser
import requests

from ..config import FETCH_TIMEOUT, FETCH_UA, RSS_ENTRIES_PER_FEED
from ..models import Candidate
from .extract import html_to_text


def fetch_rss(source: Source) -> list[Candidate]:
    try:
        res = requests.get(source.url, headers={"User-Agent": FETCH_UA}, timeout=FETCH_TIMEOUT)
        if res.status_code != 200:
            return []
        feed = feedparser.parse(res.content)
    except Exception:
        return []

    out: list[Candidate] = []
    for e in feed.entries[:RSS_ENTRIES_PER_FEED]:
        title = html.unescape((e.get("title") or "").strip())
        link = (e.get("link") or "").strip()
        if not title or not link.startswith("http"):
            continue
        t = e.get("published_parsed") or e.get("updated_parsed")
        published = calendar.timegm(t) if t else 0
        blurb = html_to_text(e.get("summary") or e.get("description") or "", cap=300)
        out.append(
            Candidate(
                source_id=source.id,
                source_label=source.label,
                title=title,
                url=link,
                published_at=published,
                blurb=blurb,
                default_category=source.default_category,
            )
        )
    return out
