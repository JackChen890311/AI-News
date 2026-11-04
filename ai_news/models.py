"""資料模型與共用小工具。"""

from __future__ import annotations

import re
from dataclasses import dataclass


def dedup_key(url: str) -> str:
    """把 URL 正規化成跨日去重的 key（拿掉 scheme、www、query、結尾斜線）。"""
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = re.sub(r"[?#].*$", "", u)
    return u.rstrip("/")


@dataclass
class Candidate:
    """一則還沒被選進 digest 的候選新聞。"""

    source_id: str
    source_label: str
    title: str
    url: str
    published_at: int = 0  # unix 秒；0 = 來源沒給日期
    score: int | None = None  # HN points
    comments: int | None = None  # HN 留言數
    hn_id: int | None = None
    hn_url: str | None = None
    blurb: str = ""  # RSS 描述，正文抓不到時的備援
    category: str | None = None  # headline | research | tools | opinion
    rank_score: float = 0.0
    picked: bool = False

    @property
    def dedup_id(self) -> str:
        """穩定去重 id：HN 用 id（同一篇被多次張貼也能去重），其他用正規化 URL。"""
        return str(self.hn_id) if self.hn_id is not None else dedup_key(self.url)
