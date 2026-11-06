"""摘要引擎。

預設走本機 OpenCode CLI（`opencode run`）—— 用你已登入的 opencode zen 模型，
不需要任何 API key。也可以 AI_NEWS_PROVIDER=api 切到任何 OpenAI 相容端點。

可靠性設計（三層）：
1. 傳輸層重試：429 / 5xx / 連線錯誤 / 空回應 / 輸出被截斷 → 指數退避 + jitter。
2. 模型層備援：主模型的每日額度用完或一直失敗 → 自動換 AI_NEWS_FALLBACK_MODELS。
3. 內容層保底：LLM 全部失敗時，呼叫端可用 extractive_summary() 從原文擷取
   標題句與重點句，保證每則都有可讀摘要（不會只剩 RSS 簡介）。

另外有全域時間預算（AI_NEWS_TIME_BUDGET），避免 GitHub Actions 被重試拖到逾時。
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
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
    JSON_MODE,
    JSON_RETRIES,
    MAX_CONTENT_CHARS,
    MAX_OUTPUT_TOKENS,
    MODEL,
    PROVIDER,
    SUMMARY_TIMEOUT,
    TIME_BUDGET,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# 這些狀態碼是暫時性的（限流、過載、閘道錯誤），值得重試。
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

# 端點是否支援 response_format={"type":"json_object"}。
# 第一次遇到 400 就永久關掉（同一個 process 內），改用提示詞約束 JSON。
_json_mode_lock = threading.Lock()
_json_mode_supported = JSON_MODE

# 全域時間預算：從 process 啟動起算，0 = 不限。
_DEADLINE: float | None = (
    time.monotonic() + TIME_BUDGET if TIME_BUDGET and TIME_BUDGET > 0 else None
)

_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s*")

# Gemini 免費層的「每日」額度用完時，重試同一個模型不會恢復，直接換模型。
_DAILY_QUOTA_RE = re.compile(
    r"per day|perday|daily limit|requests per day|free_tier_requests|insufficient_quota",
    re.I,
)


@dataclass
class Summary:
    ok: bool
    tldr: str = ""
    points: list[str] = field(default_factory=list)
    why: str = ""
    provider: str = ""
    raw: str = ""
    # llm = 模型摘要；extractive = 從原文擷取；blurb = 只有來源簡介；none = 無內容
    mode: str = ""


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


_STRICT_SUFFIX = "\n\n重要：只輸出一個 JSON 物件，不要任何其他文字、不要 markdown code fence。"


def _clean_cli_output(text: str) -> str:
    """opencode run 會印 ANSI 色碼和 '> build · model' 標頭，剝掉。"""
    text = _ANSI_RE.sub("", text or "")
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(">")]
    return "\n".join(lines).strip()


def _strip_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def _replace_smart_quotes(text: str) -> str:
    # 只在「原始 JSON 解析失敗」後才做；把模型常用的彎引號換成直引號。
    return (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )


def _parse_json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    candidate = text[start : end + 1]
    # 依序嘗試：原樣 → 只修尾逗號 → 再修彎引號。
    # 先修尾逗號可避免把字串內正常的「彎引號」誤換成 JSON 分隔符。
    repaired = _strip_trailing_commas(candidate)
    for attempt in (candidate, repaired, _replace_smart_quotes(repaired)):
        try:
            data = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        return data if isinstance(data, dict) else None
    return None


def _to_summary(data: dict | None, raw: str, provider: str) -> Summary:
    if not isinstance(data, dict):
        return Summary(ok=False, raw=raw, provider=provider)
    points = data.get("points") or []
    if isinstance(points, str):
        points = [points]
    points = [str(p).strip() for p in points if str(p).strip()]
    tldr = str(data.get("tldr") or "").strip()
    ok = bool(tldr)
    return Summary(
        ok=ok,
        tldr=tldr,
        points=points,
        why=str(data.get("why") or "").strip(),
        provider=provider,
        raw=raw,
        mode="llm" if ok else "",
    )


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip(" ,;，；") + "…"


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    out: list[str] = []
    for part in _SENTENCE_RE.split(text):
        part = part.strip(" -•\t")
        if len(part) >= 20:
            out.append(part)
    return out


def extractive_summary(title: str, text: str | None, blurb: str = "") -> Summary:
    """不靠 LLM 的保底摘要：從正文（或 RSS 簡介）擷取第一句與重點句。

    這是「一定有摘要」的最後一道防線。品質不如模型，但至少是文章內容，
    而不是空白的來源簡介。
    """
    source = (text or "").strip()
    # 有些抽取器會把文章標題放在正文開頭，會讓第一句變成標題重複。
    if source and title and source.lower().startswith(title.lower()):
        source = source[len(title) :].lstrip(" .:—–-·\n")
    source = source or (blurb or "").strip()
    sents = _sentences(source)
    if not sents:
        # 正文/簡介太短（或只有標題）：至少把現有文字截一段出來。
        basis = source or blurb or title
        return Summary(
            ok=False,
            tldr=_clip(basis, 160),
            provider="extractive",
            raw="沒有足夠正文，只有來源簡介或標題",
            mode="extractive" if source else ("blurb" if blurb else "none"),
        )

    tldr = sents[0]
    if len(tldr) < 60 and len(sents) > 1:
        tldr = f"{tldr} {sents[1]}"
    tldr = _clip(tldr, 180)

    points: list[str] = []
    for sent in sents[1:]:
        if len(points) >= 3:
            break
        if sent in tldr:
            continue
        points.append(_clip(sent, 140))

    return Summary(
        ok=False,
        tldr=tldr,
        points=points,
        provider="extractive",
        raw="LLM 摘要失敗，改用原文擷取",
        mode="extractive",
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


# ---------------------------------------------------------------- API 路徑


def _api_models() -> list[str]:
    """主模型 + 備援模型（去重、保序）。"""
    return list(dict.fromkeys(m for m in [MODEL, *API_FALLBACK_MODELS] if m))


def _log_retry(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _time_left(deadline: float | None) -> float:
    if deadline is None:
        return float("inf")
    return max(0.0, deadline - time.monotonic())


def _sleep_before_retry(
    attempt: int, retry_after: float | None, deadline: float | None = None
) -> bool:
    """重試前等待；回傳 False 代表時間預算已用完，不該再試。"""
    left = _time_left(deadline)
    if left <= 1:
        return False

    if retry_after and retry_after > 0:
        delay = min(retry_after, API_RETRY_MAX)
    else:
        delay = min(API_RETRY_BASE * (2**attempt), API_RETRY_MAX)
        delay += random.uniform(0, delay * 0.3)  # jitter，避免平行請求同時重試

    # 不要把時間預算全部睡掉 —— 留 1 秒給最後一次請求。
    delay = min(delay, left - 1)
    if delay <= 0:
        return False
    _log_retry(f"  ↻ API 暫時失敗，{delay:.1f}s 後重試（第 {attempt + 1} 次）")
    time.sleep(delay)
    return True


def _looks_like_json_mode_error(body: str) -> bool:
    b = (body or "").lower()
    return "response_format" in b or "json_object" in b or "json mode" in b


def _request_once(
    model: str,
    prompt: str,
    temperature: float = 0.3,
    deadline: float | None = None,
) -> tuple[str | None, str, float | None, bool, str | None, bool]:
    """發一次 API 請求。

    回傳 (content, error, retry_after, retryable, finish_reason, json_mode_rejected)。
    content 有值代表成功；json_mode_rejected=True 代表端點不接受 JSON mode，
    已經自動關掉，呼叫端應立刻「不計次數」重試。
    """
    global _json_mode_supported

    with _json_mode_lock:
        use_json_mode = _json_mode_supported

    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": MAX_OUTPUT_TOKENS,
    }
    if use_json_mode:
        payload["response_format"] = {"type": "json_object"}

    timeout = SUMMARY_TIMEOUT
    if deadline is not None:
        timeout = max(5, int(min(SUMMARY_TIMEOUT, _time_left(deadline))))

    try:
        res = requests.post(
            f"{API_BASE.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as e:
        return None, f"連線失敗：{e}", None, True, None, False

    body = (res.text or "")[:500]
    if res.status_code == 400 and use_json_mode and _looks_like_json_mode_error(body):
        with _json_mode_lock:
            _json_mode_supported = False
        _log_retry("  ↻ 端點不支援 response_format=json_object，改用提示詞約束 JSON")
        return None, "端點不支援 JSON mode", None, True, None, True

    if not res.ok:
        retry_after = None
        header = res.headers.get("Retry-After")
        if header:
            try:
                retry_after = float(header)
            except ValueError:
                retry_after = None
        flat = body.replace("\n", " ")
        return None, f"HTTP {res.status_code}: {flat}", retry_after, res.status_code in _RETRYABLE_STATUS, None, False

    try:
        choice = res.json()["choices"][0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):  # 少數端點回傳 parts 陣列
            content = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        finish_reason = choice.get("finish_reason")
    except (ValueError, KeyError, IndexError, TypeError) as e:
        return None, f"回應格式錯誤：{e}", None, True, None, False

    if not str(content).strip():
        return None, "空回應（content 為空）", None, True, finish_reason, False
    if finish_reason in ("length", "max_tokens"):
        return None, f"輸出被截斷（finish_reason={finish_reason}）", None, True, finish_reason, False

    return str(content), "", None, False, finish_reason, False


def _via_api(prompt: str) -> Summary:
    if not API_KEY:
        return Summary(ok=False, raw="AI_NEWS_API_KEY 未設定", provider="api", mode="none")

    errors: list[str] = []
    deadline = _DEADLINE
    max_transport_retries = max(0, API_RETRIES)
    max_json_retries = max(1, JSON_RETRIES)

    for model in _api_models():
        attempt = 0  # 傳輸層重試次數
        format_retries = 0  # JSON 格式重試次數
        current_prompt = prompt

        while attempt <= max_transport_retries and format_retries < max_json_retries:
            if _time_left(deadline) <= 1:
                errors.append("時間預算用完")
                break

            temperature = 0.0 if format_retries else 0.3
            content, error, retry_after, retryable, finish_reason, json_mode_rejected = _request_once(
                model, current_prompt, temperature, deadline
            )

            # 端點不吃 JSON mode：已經自動關閉，立刻重試且不消耗重試次數。
            if json_mode_rejected:
                continue

            if content is not None:
                summary = _to_summary(_parse_json(content), content, "api")
                if summary.ok:
                    return summary
                errors.append(f"{model}: 回應不是有效 JSON（finish_reason={finish_reason or '?'}）")
                format_retries += 1
                if format_retries >= max_json_retries:
                    break
                # 降溫 + 更嚴格的指令重試同一個模型；比直接換模型更有機會救回。
                current_prompt = prompt + _STRICT_SUFFIX
                _log_retry(f"  ↻ {model} 回覆格式不對，降溫重試（{format_retries}/{max_json_retries}）")
                time.sleep(0.5)
                continue

            errors.append(f"{model}: {error}")

            # 認證錯誤換模型也沒用，直接收工。
            if "HTTP 401" in error or "HTTP 403" in error:
                return Summary(ok=False, raw=error[:500], provider="api", mode="none")

            if attempt >= max_transport_retries or not retryable:
                break
            # Gemini 免費層的「每日」額度用完，重試同一個模型也不會恢復。
            if _DAILY_QUOTA_RE.search(error):
                _log_retry(f"  ↻ {model} 每日額度用完，換下一個模型")
                break

            if not _sleep_before_retry(attempt, retry_after, deadline):
                errors.append("時間預算用完")
                break
            attempt += 1

    raw = "API 失敗（已重試）：" + " | ".join(dict.fromkeys(errors))
    return Summary(ok=False, raw=raw[:800], provider="api", mode="none")


def summarize_article(title: str, text: str) -> Summary:
    prompt = _prompt(title, (text or "")[:MAX_CONTENT_CHARS])
    if PROVIDER == "api":
        return _via_api(prompt)
    return _via_opencode(prompt)
