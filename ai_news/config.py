"""集中設定：來源、關鍵字、分層、排序權重、路徑。

想改抓什麼、每天幾則、用哪個模型 —— 都改這個檔，
CLI / launchd cron / skill 的行為會一起變（它們共用同一份邏輯）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# ---------- 路徑 ----------

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("AI_NEWS_DATA_DIR", str(REPO_ROOT / "data")))
DIGESTS_DIR = Path(os.environ.get("AI_NEWS_DIGEST_DIR", str(REPO_ROOT / "digests")))
DB_PATH = DATA_DIR / "state.db"

# ---------- 抓取參數 ----------

FETCH_UA = "Mozilla/5.0 (compatible; AI-News-Digest/1.0)"
FETCH_TIMEOUT = 20  # 秒
RSS_ENTRIES_PER_FEED = 20
MIN_CONTENT_LENGTH = 200  # 正文比這短視為抓取失敗
MAX_CONTENT_CHARS = 12000  # 餵給摘要器的字元上限（省 token；前 12k 通常已涵蓋重點）

# ---------- 分層 ----------

CATEGORY_ORDER = ["headline", "research", "tools", "opinion"]
CATEGORY_COUNTS = {"headline": 3, "research": 2, "tools": 2, "opinion": 1}
CATEGORY_LABELS = {
    "headline": "🔥 頭條",
    "research": "🔬 研究",
    "tools": "🛠️ 工具",
    "opinion": "💭 觀點",
}

MAX_PER_SOURCE = 2  # 單一來源一天最多佔幾則（防止洗版）
DEDUP_DAYS = int(os.environ.get("AI_NEWS_DEDUP_DAYS", "14"))
CACHE_MIN_ITEMS = 6  # 今天 DB 已有這麼多則就直接用快取，不重抓
TOTAL_TARGET = sum(CATEGORY_COUNTS.values())  # 一天目標則數
HEADLINE_BACKFILL_CAP = 5  # 其他分層缺額時，頭條最多回填到幾則

# ---------- 來源 ----------


@dataclass(frozen=True)
class Source:
    id: str
    label: str
    url: str
    weight: float = 1.0  # 排序權重：官方公告 > HN > 媒體
    kind: str = "rss"  # "rss" | "hn"


SOURCES: list[Source] = [
    # HN 首頁：社群熱度當訊號
    Source("hn", "Hacker News",
           "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=60",
           weight=1.15, kind="hn"),
    # 實驗室官方公告：第一手消息
    Source("openai", "OpenAI", "https://openai.com/blog/rss.xml", weight=1.5),
    Source("deepmind", "Google DeepMind", "https://deepmind.google/blog/rss.xml", weight=1.5),
    # 媒體 AI 版
    Source("techcrunch", "TechCrunch",
           "https://techcrunch.com/category/artificial-intelligence/feed/", weight=1.1),
    Source("theverge", "The Verge",
           "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", weight=1.0),
    Source("arstechnica", "Ars Technica",
           "https://feeds.arstechnica.com/arstechnica/technology-lab", weight=1.0),
    Source("huggingface", "Hugging Face", "https://huggingface.co/blog/feed.xml", weight=1.1),
]

SOURCE_BY_ID = {s.id: s for s in SOURCES}

# 信任來源：官方實驗室 / 平台 blog，整個 feed 都是 AI 內容，
# 不用再過 AI 關鍵字（不然 "Genie 4"、"SynthID Bio" 這種標題會被誤殺）。
TRUSTED_SOURCES = {"openai", "deepmind", "huggingface"}

# ---------- 關鍵字 ----------

# AI 過濾：標題沒中這些詞就不進 pool。加字邊界（\b）避免 "ai" 配上 "chair"。
AI_KEYWORDS = [
    # 實驗室 / 公司
    "openai", "anthropic", "deepmind", "google ai", "meta ai", "mistral",
    "perplexity", r"\bxai\b", "cohere", "hugging ?face", "stability ai",
    "runway", "suno", "elevenlabs", "midjourney", "nvidia",
    "scale ai", "databricks", r"\bcursor\b", "replit", "windsurf",
    # 產品 / 模型
    "chatgpt", "gpt-?[0-9]", r"\bgpt\b", "claude", "gemini", "llama",
    "grok", r"\bo[0-9]\b", "sora", "dall-?e", "stable diffusion", "copilot",
    "codex", "devin", r"\bqwen\b", "deepseek", r"\bkimi\b", "phi-?[0-9]",
    # 通用詞
    r"\bai\b", r"\ba\.i\.", r"\bllms?\b", r"\bagi\b",
    "large language model", "machine learning", "deep learning",
    "neural network", "transformer", "diffusion model", "multimodal",
    "generative ai", r"\brag\b", "fine-?tun", "ai agent", r"\bagentic\b",
    "prompt engineering", "inference", "embedding", "vector database",
    "reinforcement learning", "foundation model", "reasoning model", "chatbot",
    "world model", "synthid", "weights",
]
AI_REGEX = re.compile("|".join(AI_KEYWORDS), re.I)

# 分層分類：照 tools > research > opinion 順序比對，都不中就是 headline。
# 「introducing / releases / launch」刻意不放在 tools —— 那種是產品發布，屬於頭條。
# 「plugin / extension」也不放 —— 安全事件新聞常常含這些詞。
TOOLS_KEYWORDS = [
    r"^show hn", "open[- ]source", "open[- ]weights", "github", r"\blibrary\b",
    r"\bframework\b", r"\bsdk\b", r"\bcli\b", "boilerplate", "template",
    "toolkit", "devtool", "self-?host", "local llm", "sandbox", "playground",
]
RESEARCH_KEYWORDS = [
    "arxiv", r"\bpapers?\b", "preprint", "benchmark", r"\bdatasets?\b",
    "research", r"\bstud(y|ies)\b", "interpretability", "alignment",
    "scaling law", "reinforcement learning", "neurips", r"\bicml\b", r"\biclr\b",
    "state-of-the-art", r"\bsota\b", "reasoning model", "chain-of-thought",
    "ablation", "safety eval",
]
OPINION_KEYWORDS = [
    r"\bwhy\b", "essay", "opinion", "thoughts on", "rant", "hot take",
    "the case (for|against)", "future of", "what (we|i) learned", "lessons",
    "review:", r"\banalysis\b", r"\bcritique\b", "i tried", "we need to",
    r"^stop\b", "stop asking", "overrated", "underrated", "is dying",
    "my take", "mistake", r"^the problem with", r"^in defense of",
    "how i ", "how we ", "should you", r"\bbubble\b",
]
OPINION_REGEX = re.compile("|".join(OPINION_KEYWORDS), re.I)

# 降權詞：官方 blog 的客戶案例、人事公告、合作新聞 —— 是 AI 內容但價值低。
NOISE_KEYWORDS = [
    r"\broi\b", "customer story", "case study", "achieves", "partnership",
    "appoints", "how .* (uses|defends|is changing|is developing|is turning)",
    "webinar", r"^meet ", "gartner", "forrester",
]
NOISE_REGEX = re.compile("|".join(NOISE_KEYWORDS), re.I)
SCORE_PENALTY = 0.5  # 命中降權詞時分數乘上這個係數
TOOLS_REGEX = re.compile("|".join(TOOLS_KEYWORDS), re.I)
RESEARCH_REGEX = re.compile("|".join(RESEARCH_KEYWORDS), re.I)
OPINION_REGEX = re.compile("|".join(OPINION_KEYWORDS), re.I)

# ---------- 摘要引擎 ----------

PROVIDER = os.environ.get("AI_NEWS_PROVIDER", "opencode")  # opencode | api
MODEL = os.environ.get("AI_NEWS_MODEL", "opencode/deepseek-v4.1-flash-free")
OPENCODE_BIN = os.environ.get("AI_NEWS_OPENCODE_BIN", "opencode")
API_BASE = os.environ.get("AI_NEWS_API_BASE", "https://api.openai.com/v1")
API_KEY = os.environ.get("AI_NEWS_API_KEY", "")
SUMMARY_TIMEOUT = int(os.environ.get("AI_NEWS_SUMMARY_TIMEOUT", "240"))
CONCURRENCY = int(os.environ.get("AI_NEWS_CONCURRENCY", "4"))

# ---------- launchd 排程 ----------

SCHEDULE_LABEL = os.environ.get("AI_NEWS_LAUNCHD_LABEL", "com.ainews.digest")
SCHEDULE_HOUR = int(os.environ.get("AI_NEWS_SCHEDULE_HOUR", "8"))
SCHEDULE_MINUTE = int(os.environ.get("AI_NEWS_SCHEDULE_MINUTE", "30"))
