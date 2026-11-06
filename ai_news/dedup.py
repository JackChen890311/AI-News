"""跨來源去重。

三層：
1. URL：由 models.canonical_url_key() 處理（只去掉追蹤參數）。
2. 標題：SequenceMatcher + 包含關係，選稿前就能過濾。
3. 摘要：文章摘要產生後，再比對 TLDR / points。這是最後一道，
   專門抓「不同網址、不同標題，但其實同一件事」的重複報導。

標題和摘要都用輕量字元／詞彙相似度，不需要額外 API 或模型。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .config import SUMMARY_DEDUP_RATIO, TITLE_DEDUP_RATIO

# 這些詞太常見，不能拿來當「同一事件」的證據。
_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "after",
    "over", "under", "about", "will", "would", "could", "should", "have",
    "has", "had", "its", "their", "his", "her", "are", "was", "were",
    "new", "news", "report", "reports", "says", "said", "announces",
    "announced", "launches", "launched", "releases", "released", "introduces",
    "introducing", "update", "updates", "ai", "artificial", "intelligence",
    "model", "models", "company", "startup", "tech", "technology",
}

_LATIN_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9.+_\-]{2,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_NORMALIZE_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = _NORMALIZE_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def title_key(title: str) -> str:
    """標題正規化：去掉 Show HN 之類的前綴與標點。"""
    key = normalize_text(title)
    return re.sub(r"^(show|ask|tell)\s+hn\s+", "", key)


def _latin_tokens(text: str) -> set[str]:
    return {
        token for token in _LATIN_TOKEN_RE.findall(normalize_text(text))
        if token not in _STOPWORDS
    }


def _cjk_bigrams(text: str) -> set[str]:
    chars = "".join(_CJK_RE.findall(text or ""))
    return {chars[i : i + 2] for i in range(len(chars) - 1)}


def text_similarity(a: str, b: str) -> tuple[float, float, float, set[str]]:
    """回傳 (綜合相似度, 序列相似度, Jaccard, 共同英文關鍵詞)。

    綜合相似度取 max：改寫幅度大的中文摘要序列相似度可能不高，
    但專有名詞與字元 bigram 重疊會補上。
    """
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0, 0.0, 0.0, set()

    seq = SequenceMatcher(None, na, nb).ratio()
    set_a = _latin_tokens(a) | _cjk_bigrams(a)
    set_b = _latin_tokens(b) | _cjk_bigrams(b)
    jaccard = len(set_a & set_b) / len(set_a | set_b) if set_a and set_b else 0.0
    shared_tokens = _latin_tokens(a) & _latin_tokens(b)
    return max(seq, jaccard), seq, jaccard, shared_tokens


def title_similarity(a: str, b: str) -> float:
    return text_similarity(a, b)[0]


def is_title_duplicate(title: str, others: list[str]) -> bool:
    """標題是否和既有標題重複（完全相同、包含、或高度相似）。"""
    key = title_key(title)
    if not key:
        return False

    for other in others:
        other_key = title_key(other)
        if not other_key:
            continue
        if key == other_key:
            return True

        short, long = sorted((key, other_key), key=len)
        if (
            len(short) >= 20
            and short in long
            and len(short) / max(1, len(long)) >= 0.55
        ):
            return True

        if SequenceMatcher(None, key, other_key).ratio() >= TITLE_DEDUP_RATIO:
            return True

    return False


def _get(item, key: str):
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def item_text(item) -> str:
    """把標題、TLDR、bullets 串成比對用的文字。"""
    parts = [str(_get(item, "title") or ""), str(_get(item, "tldr") or "")]
    points = _get(item, "points") or []
    if isinstance(points, str):
        parts.append(points)
    else:
        parts.extend(str(p) for p in points)
    return " ".join(part for part in parts if part)


def duplicate_reason(item, kept: list, ratio: float | None = None) -> str:
    """比對一則 item 和一組已保留的 item，回傳重複原因（空字串＝不重複）。"""
    threshold = SUMMARY_DEDUP_RATIO if ratio is None else ratio
    title = str(_get(item, "title") or "")
    summary = item_text(item)
    if not title:
        return ""

    for other in kept:
        other_title = str(_get(other, "title") or "")
        title_sim = title_similarity(title, other_title)
        if title_sim >= TITLE_DEDUP_RATIO:
            return f"標題相似 {title_sim:.2f}"

        other_summary = item_text(other)
        if not summary or not other_summary:
            continue
        summary_sim, _, _, shared_tokens = text_similarity(summary, other_summary)
        if summary_sim >= threshold:
            return f"摘要相似 {summary_sim:.2f}"

        # 改寫幅度大、但共享 2 個以上專有名詞（模型名、公司名）時仍視為同一事件。
        if (
            summary_sim >= max(0.50, threshold - 0.13)
            and len(shared_tokens) >= 2
            and title_sim >= 0.30
        ):
            return f"摘要＋關鍵詞重疊 {summary_sim:.2f}"

    return ""
