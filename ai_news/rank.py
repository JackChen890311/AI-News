"""排序、分類、選稿。

分數設計（跟 hn-ai-digest 同概念，拿到同一個尺度上比）：
    score = 來源權重 × 新舊衰減 × (1 + 熱度)
HN 有社群票數當熱度訊號；其他來源沒有票數，給固定小加成。
"""

from __future__ import annotations

from .config import (
    BACKFILL_MIN_RATIO,
    CATEGORY_COUNTS,
    CATEGORY_ORDER,
    HEADLINE_BACKFILL_CAP,
    MAX_PER_SOURCE,
    NOISE_REGEX,
    OPINION_REGEX,
    PICK_ORDER,
    RESEARCH_REGEX,
    SCORE_PENALTY,
    SOURCE_BY_ID,
    TITLE_DEDUP,
    TOOLS_REGEX,
    TOTAL_TARGET,
    TRUSTED_SOURCES,
    AI_REGEX,
)
from .dedup import is_title_duplicate
from .models import Candidate


def matches_ai(c: Candidate) -> bool:
    """信任來源全部放行；其他來源標題要中 AI 關鍵字。"""
    if c.source_id in TRUSTED_SOURCES:
        return True
    return bool(AI_REGEX.search(c.title or ""))


def classify(c: Candidate) -> str:
    # 分層專用來源（HF papers / Show HN / 觀點 feed）直接照來源指定
    if c.default_category:
        return c.default_category
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
    elif c.source_id == "hf_papers":
        popularity = min((c.score or 0) / 30, 2.5)  # upvotes
    elif c.source_id == "hn_show":
        popularity = min((c.score or 0) / 100, 2.0) + min((c.comments or 0) / 100, 0.8)
    else:
        popularity = 0.4  # 非 HN 沒有票數訊號，給固定小加成
    penalty = SCORE_PENALTY if NOISE_REGEX.search(c.title or "") else 1.0
    return weight * recency * (1 + popularity) * penalty


def pick(
    cands: list[Candidate],
    exclude_keys: set[str],
    now: int,
    exclude_titles: list[str] | None = None,
) -> tuple[dict[str, list[Candidate]], list[Candidate]]:
    """從候選中選出今日 digest。

    先過三層去重（URL key、最近 N 天標題、今天 pool 內的相似標題），
    再依 PICK_ORDER 填研究 / 工具 / 觀點，最後填頭條。
    回傳 (picked, pool)；某些分層候選不足時就少幾則，不硬湊。
    """
    seen_keys: set[str] = set()
    pool: list[Candidate] = []
    kept_titles: list[str] = list(exclude_titles or [])

    for c in cands:
        if not matches_ai(c):
            continue
        if c.dedup_keys & exclude_keys:
            continue
        if c.dedup_keys & seen_keys:
            continue
        if TITLE_DEDUP and is_title_duplicate(c.title, kept_titles):
            continue

        seen_keys.update(c.dedup_keys)
        kept_titles.append(c.title)
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

    # 先填研究 / 工具 / 觀點，頭條最後 —— 不然 HN 的名額會被頭條吃光
    for cat in PICK_ORDER:
        for c in pool:
            if len(picked[cat]) >= CATEGORY_COUNTS[cat]:
                break
            if c.picked:
                continue
            if cat != "headline" and c.category != cat:
                continue
            if take(c):
                picked[cat].append(c)

    # 缺額回填：只收「不比已選頭條弱太多」的候選。
    # 寧可少一則，也不拿客戶案例之類的填充物湊數。
    while (
        sum(len(v) for v in picked.values()) < TOTAL_TARGET
        and len(picked["headline"]) < HEADLINE_BACKFILL_CAP
    ):
        floor = 0.0
        if picked["headline"]:
            floor = BACKFILL_MIN_RATIO * min(c.rank_score for c in picked["headline"])
        added = False
        for c in pool:  # pool 已依分數排序，低於門檻即可停
            if c.picked:
                continue
            if c.rank_score < floor:
                break
            if take(c):
                picked["headline"].append(c)
                added = True
                break
        if not added:
            break

    return picked, pool
