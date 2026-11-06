---
name: ai-news
description: 抓取並瀏覽每日 AI 新聞摘要。當使用者問「今天 AI 有什麼新聞」「更新 AI digest」「catch up AI news」「跑一下 ai-news」時使用。
---

# AI News Digest

本機已安裝 `ai-news` CLI，專案位於 `~/Desktop/Jack/Github/AI-News`。

## 指令

一律先切到專案根目錄再執行（原因見下方「為什麼一定要在專案根目錄」）：

```bash
cd ~/Desktop/Jack/Github/AI-News
uv run python -m ai_news <子命令>
```

不想改動目前目錄時，用子 shell 包起來也一樣安全：

```bash
(cd ~/Desktop/Jack/Github/AI-News && uv run python -m ai_news digest)
```

- 產生今日摘要：`digest`
- 強制重抓：`digest --force`
- 開啟今天的 Markdown：`open`
- 看某一天：`show 2026-11-04`
- 搜尋歷史：`search "關鍵字"`

### 為什麼一定要在專案根目錄

`pyproject.toml` 設了 `[tool.uv] package = false`，專案不會被安裝進 venv，
因此 `ai_news` 只能靠「目前工作目錄」被 import。

`uv run --project ~/Desktop/Jack/Github/AI-News ...` 只指定要用哪個 venv，
**不會**把專案根目錄加進 `sys.path`；在其他目錄執行會得到
`No module named ai_news`。這不是環境壞掉，補上 `cd` 即可。

## 流程

1. 先跑 `digest`。輸出顯示「快取」代表今天已產生過，直接使用結果；使用者要求更新才加 `--force`。
2. 執行後把螢幕輸出的重點用繁體中文向使用者摘述：每個分層（頭條 / 研究 / 工具 / 觀點）各 1–2 句，附上 1–2 個最有興趣的連結。
3. 使用者想深入某一篇時，用 `search` 找歷史紀錄，或直接開啟對應的 digests/YYYY-MM-DD.md。
4. 若出現「擷取式摘要」，代表 LLM 摘要失敗、內容是從原文擷取的重點句；告知使用者可用 `digest --force` 重試。
5. 若看到「🔁 摘要去重」，代表不同來源報導了同一件事，程式已自動只留一則並補上同分層替補；這是正常行為，不用處理。

## 更新網站並發布（每次產生新摘要後必做）

GitHub Pages 直接從此 repo 發布，所以 digest 更新後一定要重建 `data/news.json` 並 push：

```bash
cd ~/Desktop/Jack/Github/AI-News
uv run python scripts/build.py
git add digests data/news.json
git commit -m "Update daily digest YYYY-MM-DD"
git push
```

注意事項：

- commit 前先確認 `git status`，只加入 `digests/` 與 `data/news.json`，不要提交 `data/state.db`、`.venv/` 等本機檔案。
- 注意 `git add digests` 會一併把工作區中「舊日期檔案的刪除」加進 commit。若 `git status` 出現非本次產生的刪除（例如 `D digests/2026-11-04.md`），先 `git checkout -- digests/YYYY-MM-DD.md` 還原再 add，避免網站歷史摘要消失。
- `data/news.json` 是從 `digests/*.md` 重建的；若工作區缺了舊日期檔案，重建後該天就會從網站消失，所以要先確認 `digests/` 完整再 build。
- 若 `git status` 顯示沒有變更（例如今天用快取、內容完全沒變），跳過 commit 和 push，並告知使用者網站已是最新。
- push 後確認輸出沒有錯誤；成功時告知使用者網站已隨 GitHub Pages 更新。
