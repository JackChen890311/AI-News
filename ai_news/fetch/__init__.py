"""平行抓取所有來源，回傳候選清單。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import SOURCES, Source
from ..models import Candidate
from .anthropic import fetch_anthropic
from .hf_papers import fetch_hf_papers
from .hn import fetch_hn, fetch_hn_show
from .rss import fetch_rss


def _fetch_one(source: Source) -> list[Candidate]:
    if source.kind == "hn":
        return fetch_hn()
    if source.kind == "hn_show":
        return fetch_hn_show()
    if source.kind == "hf_papers":
        return fetch_hf_papers(source)
    if source.kind == "anthropic":
        return fetch_anthropic()
    return fetch_rss(source)


def collect_all() -> list[Candidate]:
    """所有來源平行抓取；單一來源失敗就跳過（fail-open）。"""
    out: list[Candidate] = []
    with ThreadPoolExecutor(max_workers=max(1, len(SOURCES))) as ex:
        futures = [ex.submit(_fetch_one, s) for s in SOURCES]
        for fut in as_completed(futures):
            try:
                out.extend(fut.result())
            except Exception:
                continue
    return out
