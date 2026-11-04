# 🗞️ AI News

每日 AI 新聞摘要：多來源收集 → 排序 → 去重 → 抓正文 → 繁中摘要 → Obsidian-friendly Markdown。

## 每天長這樣

一天 8 則，分四層，每層有獨立名額，不會被單一來源洗版：

| 分層 | 名額 | 內容 |
|---|---|---|
| 🔥 頭條 | 3 | 產品發布、大新聞 |
| 🔬 研究 | 2 | 論文、benchmark、實驗室研究公告 |
| 🛠️ 工具 | 2 | 開源專案、套件、開發者工具 |
| 💭 觀點 | 1 | 分析、評論、經驗談 |

每則附：繁中 TLDR、2–4 個重點、一句「為什麼重要」、閱讀時間、HN 熱度（有的話）。

### 名額是怎麼填的

「研究 / 工具 / 觀點」有專用來源撐著，所以不會每天只剩新聞：

| 分層 | 專用來源 | 名額 |
|---|---|---|
| 🔬 研究 | HF Daily Papers（每日社群票選論文） | 2 |
| 🛠️ 工具 | HN Show HN（24 小時內、≥5 分） | 2 |
| 💭 觀點 | Simon Willison、Latent Space、HN 上的經驗談 | 1 |

選稿順序是**先填研究/工具/觀點、頭條最後**，避免頭條把 HN 的來源名額吃光。
真的湊不滿時才回填頭條（最多 5 則），而且只收「分數不低於已選最弱頭條 50%」的候選 ——
寧可少一則，也不拿填充物湊數。

## 運作方式

```
Hacker News 首頁 + Show HN
+ OpenAI / DeepMind / Hugging Face / TechCrunch / Verge / Ars RSS
+ HF Daily Papers + Simon Willison / Latent Space
  → AI 關鍵字過濾（官方 lab 與論文池直接放行）
  → 跨來源排序（新舊 × 來源權重 × 社群熱度；客戶案例等噪音降權）
  → 先填研究/工具/觀點，頭條最後；單一來源最多 2 則
  → 比對 SQLite 最近 14 天去重
  → 抓正文（trafilatura；失敗用 HN 熱門留言墊）
  → OpenCode CLI 摘要成繁中 JSON
  → 寫進 digests/YYYY-MM-DD.md + index.md
```

全部 fail-open：單一來源掛掉、某篇摘要失敗，digest 照樣產出。

同一份選稿核心被三個介面共用，所以手動跑和排程跑看到的結果一致：

```
        ┌─────────────────────────────┐
        │  core（ai_news/）        │
        │  sources → rank → dedup →   │
        │  extract → summarize → store │
        └──────────────┬──────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
     launchd cron   CLI 手動跑      skill（對 agent 說「今天 AI 新聞」）
```

## 快速開始

需要 [uv](https://docs.astral.sh/uv/) 和本機已登入的 [OpenCode](https://opencode.ai)（摘要引擎預設用它，免 API key）。

```bash
git clone https://github.com/jackchen890311/AI-News.git
cd AI-News
uv sync                       # 建立虛擬環境 + 安裝依賴
uv run python -m ai_news digest         # 產生今日摘要
uv run python -m ai_news open           # 用預設程式開啟今天的 markdown
```

之後每天都只要：

```bash
uv run python -m ai_news digest
```

### 安裝每日排程（macOS launchd）

```bash
uv run python -m ai_news install        # 每天 08:30 自動跑（時間可用環境變數改）
uv run python -m ai_news uninstall      # 移除
```

排程跑起來的機器要先開著；錯過的排程會在你解鎖 / 喚醒後補跑。

## CLI

```bash
uv run python -m ai_news digest                    # 產生今日摘要（今天已有會直接用快取）
uv run python -m ai_news digest --force            # 忽略快取重抓
uv run python -m ai_news digest --limit 2          # 只處理前 2 則（測試摘要引擎用）
uv run python -m ai_news digest --no-summarize     # 只看選稿結果，不呼叫 LLM
uv run python -m ai_news open                      # 開啟今日（或最新）摘要
uv run python -m ai_news show 2026-11-04           # 看某一天
uv run python -m ai_news search "DeepSeek"         # 搜尋歷史摘要（標題 / 摘要 / 重點）
```

## 設定

全部集中在一個檔案：[`ai_news/config.py`](ai_news/config.py)。
改來源、關鍵字、每天幾則、模型 —— CLI / cron / skill 行為會一起變。

常用環境變數（見 [.env.example](.env.example)）：

| 變數 | 預設 | 說明 |
|---|---|---|
| `AI_NEWS_PROVIDER` | `opencode` | `opencode`（本機 CLI）或 `api`（OpenAI 相容端點） |
| `AI_NEWS_MODEL` | `opencode/deepseek-v4.1-flash-free` | 摘要用的模型 |
| `AI_NEWS_DEDUP_DAYS` | `14` | 跨日去重天數 |
| `AI_NEWS_DIGEST_DIR` | `./digests` | 輸出目錄；可直接指到 Obsidian vault |
| `AI_NEWS_OBSIDIAN` | — | 設 `1` 時 `ai-news open` 用 `obsidian://` 開啟 |
| `AI_NEWS_SCHEDULE_HOUR` / `AI_NEWS_SCHEDULE_MINUTE` | `8` / `30` | launchd 排程時間 |

### 接到 Obsidian

兩種做法擇一：

1. 設 `AI_NEWS_DIGEST_DIR=/path/to/your/vault/AI-News`，摘要直接寫進 vault；
2. 或維持預設輸出，在 Obsidian vault 裡建 symlink 指向本 repo 的 `digests/`。

再設 `AI_NEWS_OBSIDIAN=1` 讓 `ai-news open` 直接跳進 Obsidian。

### 不用 OpenCode

任何 OpenAI 相容 API 都可以：

```bash
export AI_NEWS_PROVIDER=api
export AI_NEWS_API_BASE=https://api.openai.com/v1   # 或其他相容端點
export AI_NEWS_API_KEY=sk-...
export AI_NEWS_MODEL=gpt-4o-mini
```

## Skill（在 Claude Code / OpenCode 裡說一聲就跑）

把 [`skill/`](skill/) 複製到對應位置：

```bash
# OpenCode（全域）
cp skill/SKILL.md ~/.config/opencode/skills/ai-news/SKILL.md   # 先建 ai-news 目錄

# Claude Code（全域）
cp skill/SKILL.md ~/.claude/skills/ai-news/SKILL.md
```

之後對 agent 說「今天 AI 有什麼新聞」「更新 AI digest」就會執行並幫你摘述。

## 專案結構

```
AI-News/
├── ai_news/
│   ├── config.py        # ← 所有設定：來源、關鍵字、分層、模型、路徑
│   ├── models.py        # Candidate 資料模型、URL 去重 key
│   ├── fetch/           # HN 首頁 / Show HN / RSS / HF Daily Papers / 正文抽取
│   ├── rank.py          # 評分、分類（頭條/研究/工具/觀點）、選稿
│   ├── summarize.py     # 摘要引擎（OpenCode CLI / OpenAI 相容 API）
│   ├── store.py         # SQLite：今日快取 + 跨日去重 + 歷史搜尋
│   ├── render.py        # Markdown 輸出 + index.md
│   └── cli.py           # digest / open / show / search / install / uninstall
├── digests/             # 每日輸出（gitignored）
└── skill/SKILL.md       # agent skill
```

## 已知限制

- 排程跑在本機，電腦要開著（要雲端可改用 GitHub Actions cron 呼叫同一個 CLI + API 摘要）。
- 有些站會擋爬蟲（paywall / 403），這時自動退回 RSS 簡介或 HN 留言，摘要品質會下降。
- 免費模型的摘要偶爾格式跑掉；解析失敗會退回來源簡介並標註「未摘要」，跑 `--force` 可重試。

## License

MIT
