"""摘要引擎。

預設走本機 OpenCode CLI（`opencode run`）—— 用你已登入的 opencode zen 模型，
不需要任何 API key。也可以 AI_NEWS_PROVIDER=api 切到任何 OpenAI 相容端點。

要求模型輸出嚴格 JSON；解析失敗就回 ok=False，呼叫端退回 RSS blurb，
digest 不會因為單篇摘要失敗而開天窗（fail-open）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    API_BASE,
    API_KEY,
    MAX_CONTENT_CHARS,
    MODEL,
    PROVIDER,
    SUMMARY_TIMEOUT,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


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


def _via_api(prompt: str) -> Summary:
    try:
        import requests

        res = requests.post(
            f"{API_BASE.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            },
            timeout=SUMMARY_TIMEOUT,
        )
        res.raise_for_status()
        content = res.json()["choices"][0]["message"]["content"] or ""
    except Exception as e:
        return Summary(ok=False, raw=f"API 失敗：{e}", provider="api")
    return _to_summary(_parse_json(content), content, "api")


def summarize_article(title: str, text: str) -> Summary:
    prompt = _prompt(title, (text or "")[:MAX_CONTENT_CHARS])
    if PROVIDER == "api":
        return _via_api(prompt)
    return _via_opencode(prompt)
