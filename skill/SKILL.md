---
name: ai-news
description: 抓取並瀏覽每日 AI 新聞摘要。當使用者問「今天 AI 有什麼新聞」「更新 AI digest」「catch up AI news」「跑一下 ai-news」時使用。
---

# AI News Digest

本機已安裝 `ai-news` CLI，專案位於 `~/Desktop/Jack/Github/AI-News`。

## 指令（一律用 `uv run --project ~/Desktop/Jack/Github/AI-News python -m ai_news ...`）

- 產生今日摘要：`digest`
- 強制重抓：`digest --force`
- 開啟今天的 Markdown：`open`
- 看某一天：`show 2026-11-04`
- 搜尋歷史：`search "關鍵字"`

## 流程

1. 先跑 `digest`。輸出顯示「快取」代表今天已產生過，直接使用結果；使用者要求更新才加 `--force`。
2. 執行後把螢幕輸出的重點用繁體中文向使用者摘述：每個分層（頭條 / 研究 / 工具 / 觀點）各 1–2 句，附上 1–2 個最有興趣的連結。
3. 使用者想深入某一篇時，用 `search` 找歷史紀錄，或直接開啟對應的 digests/YYYY-MM-DD.md。
4. 若出現「未摘要」，告知使用者該篇只有來源簡介（LLM 摘要失敗），可用 `digest --force` 重試。
