"""Hugging Face Daily Papers：每日社群票選論文，天然就是「研究」層的貨源。

API 回傳的每篇有 title / summary / upvotes / submittedOnDailyAt，
用 upvotes 當熱度訊號、submittedOnDailyAt 當新鮮度。
"""

from __future__ import annotations

from datetime import datetime

import requests

from ..config import FETCH_TIMEOUT, FETCH_UA, Source
from ..models import Candidate

API = "https://huggingface.co/api/daily_papers?limit=30"


def _ts(value: str | None) -> int:
    if not value:
        return 0
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def fetch_hf_papers(source: Source) -> list[Candidate]:
    try:
        res = requests.get(API, headers={"User-Agent": FETCH_UA}, timeout=FETCH_TIMEOUT)
        res.raise_for_status()
        items = res.json()
    except Exception:
        return []

    out: list[Candidate] = []
    for item in items:
        p = item.get("paper") or {}
        paper_id = p.get("id")
        title = (p.get("title") or "").strip()
        if not paper_id or not title:
            continue
        out.append(
            Candidate(
                source_id=source.id,
                source_label=source.label,
                title=title,
                url=f"https://huggingface.co/papers/{paper_id}",
                published_at=_ts(p.get("submittedOnDailyAt") or p.get("publishedAt")),
                score=int(p.get("upvotes") or 0),
                blurb=(p.get("summary") or "").strip()[:300],
                default_category=source.default_category,
            )
        )
    return out
