"""摘要引擎。

預設走本機 OpenCode CLI（`opencode run`）—— 用你已登入的 opencode zen 模型，
不需要任何 API key。也可以 AI_NEWS_PROVIDER=api 切到任何 OpenAI 相容端點。

要求模型輸出嚴格 JSON；解析失敗就回 ok=False，呼叫端退回 RSS blurb，
digest 不會因為單篇摘要失敗而開天窗（fail-open）。
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .config import (
    API_BASE,
    API_FALLBACK_MODELS,
    API_KEY,
    API_RETRIES,
    API_RETRY_BASE,
    API_RETRY_MAX,
    MAX_CONTENT_CHARS,
    MODEL,
    PROVIDER,
    SUMMARY_TIMEOUT,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# 這些狀態碼是暫時性的（限流、過載、閘道錯誤），值得重試。
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass
class Summary:
    ok: bool
    tldr: str = ""
    points: list[str] = field(default_factory=list)
    why: str = ""
    provider: str = ""
    raw: str = ""


def reading_time(text: str | None) -> int:
    """以英文詞數粗估閱讀時間（分鐘）。"""
    words = len((text or "").split())
    return max(1, round(words / 220)) if words else 0


def _prompt(title: str, text: str) -> str:
    return f"""你是一名科技新聞編輯。請閱讀以下文章，用「繁體中文」輸出一個 JSON 物件，格式如下（不要輸出 JSON 以外的任何文字，不要用 code fence）：

{{"tldr": "一句話（80 字內）說明這篇文章在講什麼", "points": ["重點一", "重點二", "重點三"], "why": "一句話說明這則新聞為什麼重要"}}

規則：
- points 給 2 到 4 個，每個一行內寫完
- 只根據文章內容，不要杜撰；內容不足就少寫幾點
- 專有名詞、產品名保留英文

文章標題：{title}

文章內容：
{text}"""


def _clean_cli_output(text: str) -> str:
    """opencode run 會印 ANSI 色碼和 '> build · model' 標頭，剝掉。"""
    text = _ANSI_RE.sub("", text or "")
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(">")]
    return "\n".join(lines).strip()


def _parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _to_summary(data: dict | None, raw: str, provider: str) -> Summary:
    if not isinstance(data, dict):
        return Summary(ok=False, raw=raw, provider=provider)
    points = data.get("points") or []
    if isinstance(points, str):
        points = [points]
    points = [str(p).strip() for p in points if str(p).strip()]
    tldr = str(data.get("tldr") or "").strip()
    return Summary(
        ok=bool(tldr),
        tldr=tldr,
        points=points,
        why=str(data.get("why") or "").strip(),
        provider=provider,
        raw=raw,
    )


def _opencode_path() -> str:
    env = os.environ.get("AI_NEWS_OPENCODE_BIN")
    if env:
        return env
    found = shutil.which("opencode")
    if found:
        return found
    return str(Path.home() / ".opencode" / "bin" / "opencode")


def _via_opencode(prompt: str) -> Summary:
    try:
        proc = subprocess.run(
            [_opencode_path(), "run", "--model", MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=SUMMARY_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return Summary(ok=False, raw=f"opencode timeout（{SUMMARY_TIMEOUT}s）", provider="opencode")
    except FileNotFoundError as e:
        return Summary(ok=False, raw=f"找不到 opencode：{e}", provider="opencode")

    out = _clean_cli_output(proc.stdout or "")
    err = _clean_cli_output(proc.stderr or "")
    if not out and proc.returncode != 0:
        return Summary(ok=False, raw=err.strip()[:500], provider="opencode")
    if "limit reached" in out.lower() or "limit reached" in err.lower():
        return Summary(
            ok=False,
            raw="OpenCode 免費額度已用完：換 AI_NEWS_MODEL、訂閱 OpenCode Go，或改 AI_NEWS_PROVIDER=api",
            provider="opencode",
        )
    return _to_summary(_parse_json(out), out, "opencode")


def _api_models() -> list[str]:
    """主模型 + 備援模型（去重、保序）。"""
    return list(dict.fromkeys(m for m in [MODEL, *API_FALLBACK_MODELS] if m))


def _log_retry(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _sleep_before_retry(attempt: int, retry_after: float | None) -> None:
    if retry_after and retry_after > 0:
        delay = min(retry_after, API_RETRY_MAX)
    else:
        delay = min(API_RETRY_BASE * (2**attempt), API_RETRY_MAX)
        delay += random.uniform(0, delay * 0.3)  # jitter，避免平行請求同時重試
    _log_retry(f"  ↻ API 暫時失敗，{delay:.1f}s 後重試（{attempt + 1}/{API_RETRIES}）")
    time.sleep(delay)


def _request_once(model: str, prompt: str) -> tuple[str | None, str, float | None, bool]:
    """回傳 (content, error, retry_after, retryable)。content 有值代表成功。"""
    try:
        res = requests.post(
            f"{API_BASE.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
            timeout=SUMMARY_TIMEOUT,
        )
    except requests.RequestException as e:
        return None, f"連線失敗：{e}", None, True

    if res.ok:
        try:
            content = res.json()["choices"][0]["message"]["content"] or ""
            return content, "", None, False
        except (ValueError, KeyError, IndexError, TypeError) as e:
            return None, f"回應格式錯誤：{e}", None, False

    body = (res.text or "")[:300].replace("\n", " ")
    retry_after = None
    header = res.headers.get("Retry-After")
    if header:
        try:
            retry_after = float(header)
        except ValueError:
            retry_after = None
    return None, f"HTTP {res.status_code}: {body}", retry_after, res.status_code in _RETRYABLE_STATUS


def _via_api(prompt: str) -> Summary:
    if not API_KEY:
        return Summary(ok=False, raw="AI_NEWS_API_KEY 未設定", provider="api")

    errors: list[str] = []
    for model in _api_models():
        for attempt in range(API_RETRIES + 1):
            content, error, retry_after, retryable = _request_once(model, prompt)
            if content is not None:
                summary = _to_summary(_parse_json(content), content, "api")
                if summary.ok:
                    return summary
                errors.append(f"{model}: 回應不是有效 JSON")
                break  # 格式問題重試同一個模型沒用，直接換下一個模型
            errors.append(f"{model}: {error}")
            # 認證錯誤換模型也沒用，直接收工。
            if "HTTP 401" in error or "HTTP 403" in error:
                return Summary(ok=False, raw=error[:500], provider="api")
            if attempt >= API_RETRIES or not retryable:
                break
            # Gemini 免費層的「每日」額度用完，重試同一個模型也不會恢復。
            if re.search(r"per day|perday|daily limit|PerDay", error, re.I):
                break
            _sleep_before_retry(attempt, retry_after)

    raw = "API 失敗（已重試）：" + " | ".join(errors)
    return Summary(ok=False, raw=raw[:800], provider="api")


def summarize_article(title: str, text: str) -> Summary:
    prompt = _prompt(title, (text or "")[:MAX_CONTENT_CHARS])
    if PROVIDER == "api":
        return _via_api(prompt)
    return _via_opencode(prompt)
