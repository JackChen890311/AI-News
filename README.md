# 🗞️ AI News

每日 AI 新聞摘要：多來源收集 → 排序 → 去重 → 抓正文 → 繁中摘要 → Obsidian-friendly Markdown，並附一個可直接部署的靜態新聞網站。

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
| 💭 觀點 | Latent Space、HN 上的經驗談 | 1 |

選稿順序是**先填研究/工具/觀點、頭條最後**，避免頭條把 HN 的來源名額吃光。
真的湊不滿時才回填頭條（最多 5 則），而且只收「分數不低於已選最弱頭條 50%」的候選 ——
寧可少一則，也不拿填充物湊數。

## 運作方式

```
Hacker News 首頁 + Show HN
+ OpenAI / Anthropic（Claude）/ DeepMind / Hugging Face / TechCrunch / Verge / Ars
+ HF Daily Papers + Latent Space
  → AI 關鍵字過濾（官方 lab 與論文池直接放行）
  → 跨來源排序（新舊 × 來源權重 × 社群熱度；客戶案例等噪音降權）
  → 先填研究/工具/觀點，頭條最後；單一來源最多 2 則
  → 三層去重：URL key → 標題相似 → 摘要相似（最近 14 天＋當日 pool）
  → 抓正文（trafilatura；失敗用 HN 熱門留言墊）
  → OpenCode CLI 摘要成繁中 JSON
  → 寫進 digests/YYYY-MM-DD.md + index.md
```

全部 fail-open：單一來源掛掉、某篇摘要失敗，digest 照樣產出。

### 去重三層

跨來源的重複報導用三道關卡處理，越後面越寬鬆、也越能抓到改寫：

1. **URL**：只移除追蹤參數（`utm_*`、`fbclid`、`ref` 等），保留 `?v=`、`?id=` 這類內容識別碼；同一篇從 HN 和 RSS 進來一定會撞 key。
2. **標題**：正規化後比對完全相同、包含關係與相似度（預設 `0.82`）。選稿前就過濾，跨日與當日 pool 都會比。
3. **摘要**：文章摘要產生後，再用標題＋TLDR＋重點條列比對一次（預設 `0.65`）。專門抓「不同網址、不同標題，但其實同一件事」的改寫；被淘汰的缺額會用同分層的替補補上。

標題與摘要都是本機字元／詞彙相似度，不需要額外 API。門檻可用環境變數調整或整層關掉。


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

### 雲端排程（GitHub Actions，不用開電腦）

[`.github/workflows/digest.yml`](.github/workflows/digest.yml) 每天 08:30（台北時間）自動跑，
用 Gemini 的 OpenAI 相容端點做摘要，跑完把 `digests/` 與 `data/news.json` commit 回 repo，
GitHub Pages 就跟著更新。

一次性設定：

1. 到 <https://aistudio.google.com/apikey> 申請 Gemini API key。
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**
   新增 `AI_NEWS_API_KEY`，值是那把 key。**不要**把 key 寫進 workflow 或 `.env` 後 commit。
3. （可選）同一頁的 **Variables** 新增 `AI_NEWS_MODEL` 覆蓋預設模型，
   例如 `gemini-3.5-flash`（預設是 `gemini-3.5-flash-lite`）。

要手動測試：Actions → **Daily AI News Digest** → **Run workflow**；
勾 `force` 會忽略當天既有的 `digests/YYYY-MM-DD.md` 重新產生。

CI 每次都是乾淨的 checkout，沒有本機的 `data/state.db`。專案會自動從已入版控的
`digests/*.md` 回填跨日去重資料（URL key 與標題），所以雲端跑出來的選稿結果和本機一致，
不會每天重複同一批頭條。

免費層 API 偶爾會回 429（限流）、503（過載），或回了空字串／被截斷的 JSON。
摘要引擎分成三層處理：

1. **傳輸層重試**：429/5xx/連線錯誤/空回應 → 依 `Retry-After` 或指數退避 + jitter 重試，
   並有全域時間預算（`AI_NEWS_TIME_BUDGET`）避免把 GitHub Actions 拖到逾時。
2. **格式重試 + 換模型**：回覆不是有效 JSON 時，先用 `temperature=0` 加嚴格的「只輸出 JSON」
   指令重試同一個模型；仍失敗才換 `AI_NEWS_FALLBACK_MODELS`（不同額度池）。
   端點若不支援 `response_format=json_object` 會自動退回提示詞約束，不會因此失敗。
3. **擷取式保底**：真的全部失敗時，從文章正文擷取第一句與重點句，組成 TLDR 與 bullets。
   **每則一定有可讀摘要**，只是會標註「⚠️ 擷取式摘要（LLM 失敗）」。

workflow 預設把 `AI_NEWS_CONCURRENCY` 降到 2，減少撞到每分鐘限流的機會。

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

## 靜態網站

repo 內附一個零依賴的靜態網站（`index.html` + `assets/`），會把 `digests/*.md` 轉成 `data/news.json`，提供每日新聞、分類篩選、搜尋與日期切換。

```bash
uv run python scripts/build.py       # digests/*.md → data/news.json
python -m http.server 8000           # 開啟 http://localhost:8000
```

`data/news.json` 已納入版控，部署時把整個 repo 當靜態站即可（GitHub Pages / Vercel / Cloudflare Pages）。
每次更新 digest 後記得重新執行 `scripts/build.py` 並提交 `data/news.json`：

```bash
uv run python -m ai_news digest
uv run python scripts/build.py
git add digests data/news.json && git commit -m "Update daily digest"
```

## 設定

全部集中在一個檔案：[`ai_news/config.py`](ai_news/config.py)。
改來源、關鍵字、每天幾則、模型 —— CLI / cron / skill 行為會一起變。

常用環境變數（見 [.env.example](.env.example)）：

| 變數 | 預設 | 說明 |
|---|---|---|
| `AI_NEWS_PROVIDER` | `opencode` | `opencode`（本機 CLI）或 `api`（OpenAI 相容端點） |
| `AI_NEWS_MODEL` | `opencode/deepseek-v4.1-flash-free` | 摘要用的模型 |
| `AI_NEWS_API_RETRIES` | `4` | API 暫時失敗（429/5xx/連線）時同模型重試次數 |
| `AI_NEWS_API_RETRY_BASE` | `2.0` | 指數退避起始秒數（2→4→8…，含 jitter） |
| `AI_NEWS_FALLBACK_MODELS` | — | 逗號分隔的備援模型；主模型額度用完或持續失敗時換手 |
| `AI_NEWS_JSON_RETRIES` | `2` | 回覆不是 JSON 時，同模型降溫重試次數 |
| `AI_NEWS_MAX_TOKENS` | `2048` | 單篇輸出上限（推理模型的 thinking token 也算在內） |
| `AI_NEWS_TIME_BUDGET` | `0`（不限） | 全域重試時間預算（秒）；CI 建議 `780` |
| `AI_NEWS_DEDUP_DAYS` | `14` | 跨日去重天數（URL 與標題都看這個範圍） |
| `AI_NEWS_TITLE_DEDUP_RATIO` | `0.82` | 標題相似度門檻（0–1） |
| `AI_NEWS_SUMMARY_DEDUP_RATIO` | `0.65` | 摘要相似度門檻（0–1） |
| `AI_NEWS_TITLE_DEDUP` | `1` | 設 `0` 關掉標題去重（除錯用） |
| `AI_NEWS_SUMMARY_DEDUP` | `1` | 設 `0` 關掉摘要去重（除錯用） |
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
│   ├── models.py        # Candidate 資料模型、URL 正規化與去重 key
│   ├── dedup.py         # 標題／摘要相似度去重
│   ├── fetch/           # HN 首頁 / Show HN / RSS / HF Daily Papers / Anthropic 官方 news / 正文抽取
│   ├── rank.py          # 評分、分類（頭條/研究/工具/觀點）、選稿與去重
│   ├── summarize.py     # 摘要引擎（重試 / 換模型 / JSON 修復 / 擷取式保底）
│   ├── store.py         # SQLite：今日快取 + 跨日 URL/標題去重 + 歷史搜尋
│   ├── render.py        # Markdown 輸出 + index.md
│   └── cli.py           # digest / open / show / search / install / uninstall
├── digests/             # 每日 Markdown 輸出（也是網站建置來源）
├── scripts/build.py     # digests/*.md → data/news.json
├── tests/               # 摘要可靠性與去重測試（uv run pytest）
├── data/news.json       # 網站資料（建置產物，已入版控）
├── assets/              # 網站樣式與前端邏輯
├── index.html           # 靜態網站入口
└── skill/SKILL.md       # agent skill
```

## 已知限制

- 本機排程（launchd）需要電腦開著；想完全不開機就用上面的 GitHub Actions 雲端排程。
- 有些站會擋爬蟲（paywall / 403），這時自動退回 RSS 簡介或 HN 留言，摘要品質會下降。
- 免費模型的摘要偶爾格式跑掉；引擎會降溫重試、換模型，最後退回「擷取式摘要」（從原文抓重點句），
  所以每則一定讀得到內容，但品質不如模型摘要。額度恢復後跑 `--force` 可重摘要。

## License

MIT
