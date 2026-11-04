"""Hacker News：Algolia 首頁 API 抓候選；Firebase API 抓留言當正文備援。"""

from __future__ import annotations

import html
import re

import requests

from ..config import FETCH_TIMEOUT, FETCH_UA
from ..models import Candidate

ALGOLIA = "https://hn.algolia.com/api/v1/search"
FIREBASE = "https://hacker-news.firebaseio.com/v0"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def fetch_hn() -> list[Candidate]:
    try:
        res = requests.get(
            f"{ALGOLIA}?tags=front_page&hitsPerPage=60",
            headers={"User-Agent": FETCH_UA},
            timeout=FETCH_TIMEOUT,
        )
        res.raise_for_status()
        hits = res.json().get("hits", [])
    except Exception:
        return []

    out: list[Candidate] = []
    for h in hits:
        if not h.get("url") or not h.get("title"):
            continue  # Ask HN 等無外部連結的貼文跳過
        try:
            hn_id = int(h["objectID"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(
            Candidate(
                source_id="hn",
                source_label="Hacker News",
                title=h["title"],
                url=h["url"],
                published_at=int(h.get("created_at_i") or 0),
                score=h.get("points"),
                comments=h.get("num_comments"),
                hn_id=hn_id,
                hn_url=f"https://news.ycombinator.com/item?id={hn_id}",
            )
        )
    return out


def _item(item_id: int) -> dict | None:
    try:
        res = requests.get(f"{FIREBASE}/item/{item_id}.json", timeout=FETCH_TIMEOUT)
        res.raise_for_status()
        return res.json()
    except Exception:
        return None


def fetch_hn_comments(story_id: int, limit: int = 5) -> str | None:
    """抓前幾則熱門留言，串成文字。正文抓不到時（paywall / 反爬）的最後備援。"""
    story = _item(story_id)
    kids = (story or {}).get("kids") or []
    texts: list[str] = []
    for kid in kids[:limit]:
        c = _item(kid) or {}
        body = c.get("text") or ""
        if not body or "[dead]" in body:
            continue
        body = _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", body))).strip()
        if body:
            texts.append(f"[{c.get('by') or '?'}]: {body}")
    return "\n\n".join(texts) if texts else None
