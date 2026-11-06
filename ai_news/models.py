"""資料模型與共用小工具。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit

# 只拿掉追蹤參數；像 YouTube 的 ?v=、CMS 的 ?id= 是內容識別碼，必須保留。
_TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid",
    "igshid", "si", "ref", "ref_src", "source", "campaign",
}
_TRACKING_PREFIXES = ("utm_",)


def dedup_key(url: str) -> str:
    """舊版 URL key（保留給既有 seen 表與歷史資料比對）。

    拿掉 scheme、www、所有 query、結尾斜線。
    """
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = re.sub(r"[?#].*$", "", u)
    return u.rstrip("/")


def canonical_url_key(url: str) -> str:
    """新的 URL key：保留有識別性的 query，只移除追蹤參數。

    例：
      https://www.example.com/a/?utm_source=x&id=2#top  -> example.com/a?id=2
      https://youtube.com/watch?v=abc                     -> youtube.com/watch?v=abc
      https://youtube.com/watch?v=xyz                     -> youtube.com/watch?v=xyz
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw

    try:
        parts = urlsplit(raw)
    except ValueError:
        return dedup_key(url)

    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith(("m.", "amp.")) and host.count(".") >= 2:
        host = host.split(".", 1)[1]

    try:
        port = parts.port
    except ValueError:
        port = None
    if port and not (
        (parts.scheme == "http" and port == 80)
        or (parts.scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"

    path = parts.path or "/"
    path = re.sub(r"/+$", "", path)
    path = re.sub(r"/amp$", "", path, flags=re.I)

    query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lower = key.lower()
        if lower in _TRACKING_PARAMS or lower.startswith(_TRACKING_PREFIXES):
            continue
        query.append((key, value))
    query.sort()

    qs = "&".join(f"{k}={v}" for k, v in query)
    return f"{host}{path}" + (f"?{qs}" if qs else "")


def url_keys(url: str) -> set[str]:
    """同時回傳新舊 key，讓歷史資料和新的正規化可以並存。"""
    return {k for k in (dedup_key(url), canonical_url_key(url)) if k}


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
    default_category: str | None = None  # 分層專用來源直接指定分類
    rank_score: float = 0.0
    picked: bool = False

    @property
    def dedup_keys(self) -> set[str]:
        """所有可用來比對的 key：URL（新舊格式）＋ HN id。

        同一篇文章無論從 HN 還是 RSS 進來，URL key 一定重疊，
        所以跨來源去重不會再漏。
        """
        keys = url_keys(self.url)
        if self.hn_id is not None:
            keys.add(f"hn:{self.hn_id}")
            keys.add(str(self.hn_id))  # 相容舊版 DB 直接存 id 的格式
        return keys

    @property
    def dedup_id(self) -> str:
        """存進 DB 的單一 key：優先使用正規化 URL，沒有 URL 才用 HN id。"""
        canonical = canonical_url_key(self.url)
        if canonical:
            return canonical
        if self.hn_id is not None:
            return f"hn:{self.hn_id}"
        return dedup_key(self.url)
