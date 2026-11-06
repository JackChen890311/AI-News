"""摘要引擎的可靠性測試：重試、JSON 修復、換模型、擷取式保底。"""

from __future__ import annotations

import json

import pytest

import ai_news.summarize as s

VALID = json.dumps(
    {"tldr": "測試摘要", "points": ["重點一", "重點二"], "why": "為什麼重要"},
    ensure_ascii=False,
)


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    """把重試間隔縮成 0，並固定測試用的設定。"""
    monkeypatch.setattr(s, "API_KEY", "test-key")
    monkeypatch.setattr(s, "MODEL", "main-model")
    monkeypatch.setattr(s, "API_FALLBACK_MODELS", [])
    monkeypatch.setattr(s, "API_RETRIES", 2)
    monkeypatch.setattr(s, "JSON_RETRIES", 2)
    monkeypatch.setattr(s, "_DEADLINE", None)
    monkeypatch.setattr(s, "_json_mode_supported", True)
    monkeypatch.setattr(s.time, "sleep", lambda *_: None)


def test_empty_response_is_retried(monkeypatch):
    calls: list[dict] = []

    def fake(model, prompt, temperature=0.3, deadline=None):
        calls.append({"model": model, "temperature": temperature})
        if len(calls) == 1:
            return None, "空回應（content 為空）", None, True, None, False
        return VALID, "", None, False, "stop", False

    monkeypatch.setattr(s, "_request_once", fake)
    result = s._via_api("prompt")

    assert result.ok
    assert result.mode == "llm"
    assert len(calls) == 2


def test_bad_json_retries_same_model_with_lower_temperature(monkeypatch):
    calls: list[dict] = []

    def fake(model, prompt, temperature=0.3, deadline=None):
        calls.append({"model": model, "temperature": temperature, "prompt": prompt})
        if len(calls) == 1:
            return "這不是 JSON", "", None, False, "stop", False
        return VALID, "", None, False, "stop", False

    monkeypatch.setattr(s, "_request_once", fake)
    result = s._via_api("prompt")

    assert result.ok
    assert len(calls) == 2
    assert calls[0]["temperature"] == 0.3
    assert calls[1]["temperature"] == 0.0
    assert "只輸出一個 JSON" in calls[1]["prompt"]


def test_daily_quota_switches_to_fallback_model(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(s, "API_FALLBACK_MODELS", ["backup-model"])

    def fake(model, prompt, temperature=0.3, deadline=None):
        calls.append(model)
        if model == "main-model":
            return (
                None,
                "HTTP 429: quota exceeded: per day limit reached",
                5,
                True,
                None,
                False,
            )
        return VALID, "", None, False, "stop", False

    monkeypatch.setattr(s, "_request_once", fake)
    result = s._via_api("prompt")

    assert result.ok
    assert calls == ["main-model", "backup-model"]


def test_json_mode_rejected_is_retried_without_counting(monkeypatch):
    calls: list[dict] = []

    def fake(model, prompt, temperature=0.3, deadline=None):
        calls.append({"temperature": temperature})
        if len(calls) == 1:
            return None, "端點不支援 JSON mode", None, True, None, True
        return VALID, "", None, False, "stop", False

    monkeypatch.setattr(s, "_request_once", fake)
    result = s._via_api("prompt")

    assert result.ok
    assert len(calls) == 2


def test_all_attempts_fail_returns_not_ok(monkeypatch):
    def fake(model, prompt, temperature=0.3, deadline=None):
        return None, "HTTP 503: overloaded", None, True, None, False

    monkeypatch.setattr(s, "_request_once", fake)
    result = s._via_api("prompt")

    assert not result.ok
    assert result.mode == "none"
    assert "HTTP 503" in result.raw


def test_time_budget_stops_before_any_request(monkeypatch):
    import time

    monkeypatch.setattr(s, "_DEADLINE", time.monotonic() - 1)
    calls: list[int] = []

    def fake(model, prompt, temperature=0.3, deadline=None):
        calls.append(1)
        return VALID, "", None, False, "stop", False

    monkeypatch.setattr(s, "_request_once", fake)
    result = s._via_api("prompt")

    assert not result.ok
    assert calls == []
    assert "時間預算用完" in result.raw


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


def test_request_once_treats_empty_content_as_retryable(monkeypatch):
    monkeypatch.setattr(
        s.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200, {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
        ),
    )
    content, error, _, retryable, _, rejected = s._request_once("m", "p")

    assert content is None
    assert retryable is True
    assert rejected is False
    assert "空回應" in error


def test_request_once_treats_truncated_output_as_retryable(monkeypatch):
    monkeypatch.setattr(
        s.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            {
                "choices": [
                    {"message": {"content": '{"tldr": "未完'}, "finish_reason": "length"}
                ]
            },
        ),
    )
    content, error, _, retryable, finish, _ = s._request_once("m", "p")

    assert content is None
    assert retryable is True
    assert finish == "length"
    assert "截斷" in error


def test_request_once_disables_json_mode_on_400(monkeypatch):
    responses = [
        FakeResponse(400, text="Unknown name 'response_format'"),
        FakeResponse(
            200,
            {"choices": [{"message": {"content": VALID}, "finish_reason": "stop"}]},
        ),
    ]
    payloads: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        payloads.append(json)
        return responses[len(payloads) - 1]

    monkeypatch.setattr(s.requests, "post", fake_post)

    content, _, _, _, _, rejected = s._request_once("m", "p")
    assert content is None and rejected is True
    assert "response_format" in payloads[0]

    content, _, _, _, _, rejected = s._request_once("m", "p")
    assert content == VALID and rejected is False
    assert "response_format" not in payloads[1]


def test_parse_json_repairs_fences_smart_quotes_and_trailing_commas():
    raw = '```json\n{“tldr”: “修好了”, “points”: [“a”, “b”,],}\n```'
    data = s._parse_json(raw)

    assert data == {"tldr": "修好了", "points": ["a", "b"]}


def test_parse_json_keeps_curly_quotes_inside_string_values():
    raw = '{"tldr": "他說“沒問題”", "points": ["a",],}'
    data = s._parse_json(raw)

    assert data["tldr"] == "他說“沒問題”"


def test_extractive_summary_always_returns_readable_text():
    text = (
        "OpenAI 發布新的推理模型，主打更長的自動化工作流程。"
        "這個模型在多步驟工具使用上明顯優於前一代，但成本也提高。"
        "官方表示先開放給企業客戶，之後才逐步下放。"
    )
    result = s.extractive_summary("OpenAI 新模型", text)

    assert not result.ok  # 不是 LLM 摘要
    assert result.mode == "extractive"
    assert result.tldr
    assert result.points
    assert "OpenAI" in result.tldr


def test_extractive_summary_falls_back_to_blurb_when_text_missing():
    result = s.extractive_summary("標題", "", "這是 RSS 的簡短說明。")

    assert result.tldr
    assert result.mode in {"extractive", "blurb"}
