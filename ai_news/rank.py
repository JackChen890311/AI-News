"""排序、分類、選稿。

分數設計（跟 hn-ai-digest 同概念，拿到同一個尺度上比）：
    score = 來源權重 × 新舊衰減 × (1 + 熱度)
HN 有社群票數當熱度訊號；其他來源沒有票數，給固定小加成。
"""

from __future__ import annotations

from .config import (
    CATEGORY_COUNTS,
    CATEGORY_ORDER,
    HEADLINE_BACKFILL_CAP,
    MAX_PER_SOURCE,
    NOISE_REGEX,
    OPINION_REGEX,
    RESEARCH_REGEX,
    SCORE_PENALTY,
    SOURCE_BY_ID,
    TOOLS_REGEX,
    TOTAL_TARGET,
    TRUSTED_SOURCES,
    AI_REGEX,
)
from .models import Candidate


def matches_ai(c: Candidate) -> bool:
    """信任來源全部放行；其他來源標題要中 AI 關鍵字。"""
    if c.source_id in TRUSTED_SOURCES:
        return True
    return bool(AI_REGEX.search(c.title or ""))


def classify(c: Candidate) -> str:
    t = c.title or ""
    if TOOLS_REGEX.search(t):
        return "tools"
    if RESEARCH_REGEX.search(t):
        return "research"
    # 官方實驗室 blog 的「Why we built X」其實是產品公告，不當觀點
    if c.source_id not in TRUSTED_SOURCES and OPINION_REGEX.search(t):
        return "opinion"
    return "headline"


def rank_score(c: Candidate, now: int) -> float:
    if c.published_at > 0:
        hours = max(0.0, (now - c.published_at) / 3600)
    else:
        hours = 24.0  # 沒日期的項目當作一天前，避免整批看起來都超新
    recency = 1 / (1 + hours / 18)  # 約一天長的溫和衰減
    weight = SOURCE_BY_ID[c.source_id].weight if c.source_id in SOURCE_BY_ID else 1.0
    if c.source_id == "hn":
        popularity = min((c.score or 0) / 400, 2.5) + min((c.comments or 0) / 250, 1.2)
    else:
        popularity = 0.4  # 非 HN 沒有票數訊號，給固定小加成
    penalty = SCORE_PENALTY if NOISE_REGEX.search(c.title or "") else 1.0
    return weight * recency * (1 + popularity) * penalty


def pick(
    cands: list[Candidate], exclude_keys: set[str], now: int
) -> tuple[dict[str, list[Candidate]], list[Candidate]]:
    """從候選中選出今日 digest。

    順序：先填 headline（全 pool 取最高分），再依 research / tools / opinion
    各自的 pool 填滿；全程套單一來源上限（MAX_PER_SOURCE）。
    回傳 (picked, pool)；某些分層候選不足時就少幾則，不硬湊。
    """
    seen: set[str] = set()
    pool: list[Candidate] = []
    for c in cands:
        if not matches_ai(c):
            continue
        if c.dedup_id in exclude_keys:
            continue
        if c.dedup_id in seen:
            continue
        seen.add(c.dedup_id)
        c.rank_score = rank_score(c, now)
        c.category = classify(c)
        pool.append(c)

    pool.sort(key=lambda c: c.rank_score, reverse=True)

    picked: dict[str, list[Candidate]] = {cat: [] for cat in CATEGORY_ORDER}
    used: dict[str, int] = {}

    def take(c: Candidate) -> bool:
        if used.get(c.source_id, 0) >= MAX_PER_SOURCE:
            return False
        used[c.source_id] = used.get(c.source_id, 0) + 1
        c.picked = True
        return True

    for cat in CATEGORY_ORDER:
        for c in pool:
            if len(picked[cat]) >= CATEGORY_COUNTS[cat]:
                break
            if c.picked:
                continue
            if cat != "headline" and c.category != cat:
                continue
            if take(c):
                picked[cat].append(c)

    # 缺額回填：研究 / 工具 / 觀點候選不足時，把名額讓給次高分的頭條，
    # 讓 digest 保持接近目標則數（但頭條不超過上限，不會整份都是頭條）。
    while (
        sum(len(v) for v in picked.values()) < TOTAL_TARGET
        and len(picked["headline"]) < HEADLINE_BACKFILL_CAP
    ):
        for c in pool:
            if not c.picked and take(c):
                picked["headline"].append(c)
                break
        else:
            break

    return picked, pool
