"""正文抽取：trafilatura 為主、regex 為備援；PDF 不抽正文，回傳 pdf 標記。"""

from __future__ import annotations

import html as html_mod
import re

import requests
import trafilatura

from ..config import FETCH_TIMEOUT, FETCH_UA, MAX_CONTENT_CHARS, MIN_CONTENT_LENGTH

_STYLE_RE = re.compile(r"<(script|style|noscript)\b[\s\S]*?</\1>", re.I)
_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(raw: str, cap: int = 300) -> str:
    """Worker 級的保底抽取：不依賴 DOM，只是把標籤剝掉。cap 預設 300 給 blurb 用。"""
    s = raw or ""
    s = _COMMENT_RE.sub(" ", s)
    s = _STYLE_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = html_mod.unescape(s)
    return _WS_RE.sub(" ", s).strip()[:cap]


def fetch_article_text(url: str) -> tuple[str | None, str | None]:
    """抓文章並抽出正文。回傳 (正文, pdf_url)。

    - 內容夠長 → (text, None)
    - 是 PDF → (None, url)，讓呼叫端決定怎麼處理
    - 抓不到 / 太短 → (None, None)（fail-open，呼叫端用 RSS blurb 或 HN 留言墊）
    """
    try:
        res = requests.get(url, headers={"User-Agent": FETCH_UA}, timeout=FETCH_TIMEOUT)
        if res.status_code != 200:
            return None, None
        ctype = (res.headers.get("content-type") or "").lower()
        body = res.text or ""
        if "application/pdf" in ctype or body.startswith("%PDF-"):
            return None, url

        text = ""
        try:
            text = trafilatura.extract(
                body, url=url, include_comments=False, include_tables=True
            ) or ""
        except Exception:
            text = ""
        if len(text.strip()) < MIN_CONTENT_LENGTH:
            text = html_to_text(body, cap=MAX_CONTENT_CHARS)

        text = text.strip()
        if len(text) < MIN_CONTENT_LENGTH:
            return None, None
        return text[:MAX_CONTENT_CHARS], None
    except Exception:
        return None, None
