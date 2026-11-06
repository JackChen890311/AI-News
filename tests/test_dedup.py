"""跨來源去重測試：URL 正規化、標題相似度、摘要相似度、歷史回填。"""

from __future__ import annotations

import pytest

from ai_news import dedup
from ai_news.dedup import (
    duplicate_reason,
    is_title_duplicate,
    text_similarity,
    title_key,
)
from ai_news.models import Candidate, canonical_url_key, dedup_key, url_keys
from ai_news.rank import pick
from ai_news.store import Store


# ---------------------------------------------------------------- URL


@pytest.mark.parametrize(
    "url,expected",
    [
        # 追蹤參數要拿掉
        ("https://example.com/a/?utm_source=x&utm_medium=y#top", "example.com/a"),
        ("https://example.com/a?fbclid=abc", "example.com/a"),
        ("https://example.com/a?ref=hn&si=123", "example.com/a"),
        # 有識別性的 query 要保留
        ("https://www.youtube.com/watch?v=abc123", "youtube.com/watch?v=abc123"),
        ("https://example.com/post?id=42&utm_source=rss", "example.com/post?id=42"),
        # 大小寫、尾斜線、amp / m 子網域
        ("HTTPS://WWW.Example.COM/Path/", "example.com/Path"),
        ("https://m.example.com/news/amp", "example.com/news"),
        ("https://example.com/", "example.com"),
    ],
)
def test_canonical_url_key(url, expected):
    assert canonical_url_key(url) == expected


def test_url_keys_keeps_legacy_key_for_history():
    keys = url_keys("https://www.example.com/post?id=42&utm_source=rss")
    # 新 key 保留 id，舊 key 拿掉所有 query；兩者都留給歷史比對。
    assert "example.com/post?id=42" in keys
    assert "example.com/post" in keys


def test_dedup_key_still_strips_all_query():
    assert dedup_key("https://youtube.com/watch?v=abc") == "youtube.com/watch"


def test_same_article_from_hn_and_rss_shares_a_key():
    hn = Candidate(
        source_id="hn", source_label="Hacker News",
        title="OpenAI ships a new reasoning model",
        url="https://example.com/blog/post?utm_source=hn", hn_id=123,
    )
    rss = Candidate(
        source_id="techcrunch", source_label="TechCrunch",
        title="OpenAI ships a new reasoning model",
        url="https://www.example.com/blog/post/",
    )
    assert hn.dedup_keys & rss.dedup_keys


# ---------------------------------------------------------------- 標題


def test_title_key_strips_show_hn_prefix():
    assert title_key("Show HN: I built a local LLM") == "i built a local llm"


def test_similar_titles_are_duplicates():
    assert is_title_duplicate(
        "OpenAI to acquire Pierre Computer Company, Inc.",
        ["OpenAI acquires Pierre Computer Company"],
    )
    assert is_title_duplicate(
        "Google launches Gemini 3.5 Flash with lower prices",
        ["Google launches Gemini 3.5 Flash at lower prices"],
    )


def test_different_events_are_not_duplicates():
    assert not is_title_duplicate(
        "Google launches Gemini 3.5 Flash with lower prices",
        ["Google launches Veo 4 video model"],
    )
    assert not is_title_duplicate(
        "OpenAI announces GPT-6", ["Anthropic announces Claude 5"]
    )


# ---------------------------------------------------------------- 摘要


def _item(title: str, tldr: str, points: list[str] | None = None) -> dict:
    return {"title": title, "tldr": tldr, "points": points or []}


def test_paraphrased_summaries_of_same_event_are_duplicates():
    a = _item(
        "Google 推出 Gemini 3.5 Flash",
        "Google 正式推出 Gemini 3.5 Flash，支援更長影片生成，價格調降 30%。",
        ["影片生成延長", "價格下降三成"],
    )
    b = _item(
        "Google 發表 Gemini 3.5 Flash",
        "Google 發布 Gemini 3.5 Flash，影片生成時間延長，並降價三成。",
        ["支援更長影片", "降價 30%"],
    )
    assert duplicate_reason(b, [a])


def test_different_events_are_not_summary_duplicates():
    a = _item(
        "Nvidia 公布最新財報",
        "Nvidia 公布最新財報，資料中心營收創新高。",
    )
    b = _item(
        "OpenAI 與微軟擴大合作",
        "OpenAI 與微軟宣布擴大合作，將模型部署到 Azure。",
    )
    assert not duplicate_reason(b, [a])


def test_identical_title_is_always_a_duplicate():
    a = _item("Same headline", "完全不同的摘要內容。")
    b = _item("Same headline", "另一段文字，講的是別的東西。")
    assert duplicate_reason(b, [a]).startswith("標題相似")


def test_text_similarity_handles_empty_input():
    assert text_similarity("", "abc")[0] == 0.0
    assert text_similarity("abc", "")[0] == 0.0


# ---------------------------------------------------------------- 選稿整合


def _cand(source: str, title: str, url: str, **kw) -> Candidate:
    return Candidate(
        source_id=source, source_label=source.title(),
        title=title, url=url, published_at=kw.pop("published_at", 0), **kw,
    )


def test_pick_dedupes_same_url_across_sources():
    cands = [
        _cand("hn", "OpenAI ships a new reasoning model",
              "https://example.com/post?utm_source=hn", hn_id=1),
        _cand("techcrunch", "OpenAI ships a new reasoning model",
              "https://www.example.com/post/"),
    ]
    picked, pool = pick(cands, set(), now=0)
    assert len(pool) == 1
    assert sum(len(v) for v in picked.values()) == 1


def test_pick_dedupes_similar_titles_with_different_urls():
    cands = [
        _cand("openai", "OpenAI acquires Pierre Computer Company",
              "https://openai.com/a"),
        _cand("techcrunch", "OpenAI to acquire Pierre Computer Company, Inc.",
              "https://techcrunch.com/b"),
    ]
    picked, pool = pick(cands, set(), now=0)
    assert len(pool) == 1
    assert sum(len(v) for v in picked.values()) == 1


def test_pick_respects_historical_titles():
    cands = [
        _cand("openai", "OpenAI acquires Pierre Computer Company",
              "https://openai.com/new-url"),
    ]
    picked, pool = pick(
        cands, set(), now=0,
        exclude_titles=["OpenAI to acquire Pierre Computer Company, Inc."],
    )
    assert pool == []
    assert sum(len(v) for v in picked.values()) == 0


def test_dedupe_after_summary_refills_from_reserve(monkeypatch):
    from ai_news import cli

    first = _cand("openai", "Same event headline", "https://openai.com/a")
    duplicate = _cand("techcrunch", "Same event headline", "https://techcrunch.com/b")
    replacement = _cand("deepmind", "A different research result", "https://deepmind.com/c")
    first.picked = duplicate.picked = True
    replacement.picked = False
    replacement.category = "research"

    items = [
        {
            "category": "research", "rank": 1, "title": first.title,
            "tldr": "相同事件的摘要。", "points": ["重點一"], "_candidate": first,
        },
        {
            "category": "research", "rank": 2, "title": duplicate.title,
            "tldr": "同樣事件的另一種寫法。", "points": ["重點一"], "_candidate": duplicate,
        },
    ]

    def fake_process_one(category, rank, c, no_summarize):
        return {
            "category": category, "rank": rank, "title": c.title,
            "tldr": "替補研究的摘要。", "points": ["新重點"],
            "_candidate": c,
        }

    monkeypatch.setattr(cli, "_process_one", fake_process_one)
    result = cli._dedupe_after_summary(items, [first, duplicate, replacement], False)

    assert len(result) == 2
    assert result[0]["_candidate"] is first
    assert any(it["title"] == replacement.title for it in result)
    assert all(it["_candidate"] is not duplicate for it in result)


# ---------------------------------------------------------------- 歷史回填


def test_backfill_and_recent_titles(tmp_path):
    digests = tmp_path / "digests"
    digests.mkdir()
    (digests / "2026-01-02.md").write_text(
        "### 1. [OpenAI acquires Pierre](https://www.example.com/post/?utm_source=x)\n"
        "### 2. [Show HN: local llm](https://example.org/tool)\n",
        encoding="utf-8",
    )

    store = Store(tmp_path / "state.db")
    added = store.backfill_from_digests(digests)
    assert added >= 2

    keys = store.recent_keys(100000)
    assert "example.com/post?utm_source=x" not in keys
    assert "example.com/post" in keys
    assert url_keys("https://www.example.com/post/") == {"example.com/post"}
    assert "example.org/tool" in keys

    titles = store.recent_titles(100000)
    assert "OpenAI acquires Pierre" in titles
    assert "Show HN: local llm" in titles


def test_save_item_remembers_title_and_url(tmp_path):
    store = Store(tmp_path / "state.db")
    c = _cand("openai", "A brand new model", "https://openai.com/index/model?utm_source=x")
    store.save_item(
        "2026-01-03", "headline", 1, c,
        "摘要", ["重點"], "為什麼", 2, True, "llm",
    )

    assert "A brand new model" in store.recent_titles(100000)
    keys = store.recent_keys(100000)
    assert "openai.com/index/model?utm_source=x" not in keys
    assert "openai.com/index/model" in keys
