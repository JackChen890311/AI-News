"""SQLite 狀態：今日快取、跨日去重、歷史搜尋。

一個檔案搞定，不需要任何雲端服務。預設放在 repo 的 data/state.db（gitignored），
可用 AI_NEWS_DATA_DIR 搬走。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .models import Candidate, dedup_key

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    date         TEXT NOT NULL,
    category     TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    dedup_id     TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    source_label TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    hn_url       TEXT,
    score        INTEGER,
    comments     INTEGER,
    reading_time INTEGER,
    tldr         TEXT,
    points       TEXT,
    why          TEXT,
    summarized   INTEGER DEFAULT 0,
    created_at   TEXT,
    PRIMARY KEY (date, category, rank)
);
"""

_CATEGORY_SQL_ORDER = (
    "CASE category WHEN 'headline' THEN 0 WHEN 'research' THEN 1 "
    "WHEN 'tools' THEN 2 ELSE 3 END"
)


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 摘要在 worker threads 跑，但寫入集中在這個連線（check_same_thread=False + lock）
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ---- 今日快取 ----

    def count_today(self, date: str) -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM items WHERE date = ? AND rank >= 1", (date,)
        )
        return int(cur.fetchone()[0])

    def items_for(self, date: str) -> list[dict]:
        cur = self.conn.execute(
            f"SELECT * FROM items WHERE date = ? AND rank >= 1 ORDER BY {_CATEGORY_SQL_ORDER}, rank",
            (date,),
        )
        return [dict(r) for r in cur.fetchall()]

    def replace_day(self, date: str) -> None:
        """--force 時先清掉今天的紀錄再重選。"""
        with self._lock:
            self.conn.execute("DELETE FROM items WHERE date = ?", (date,))
            self.conn.commit()

    # ---- 跨日去重 ----

    def recent_keys(self, days: int) -> set[str]:
        cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
        keys: set[str] = set()
        for r in self.conn.execute(
            "SELECT dedup_id, url FROM items WHERE date >= ?", (cutoff,)
        ):
            keys.add(r["dedup_id"])
            if r["url"]:
                keys.add(dedup_key(r["url"]))
        return keys

    # ---- 寫入 / 搜尋 ----

    def save_item(
        self,
        date: str,
        category: str,
        rank: int,
        c: Candidate,
        tldr: str,
        points: list[str],
        why: str,
        reading_time: int,
        summarized: bool,
    ) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO items
                   (date, category, rank, dedup_id, source_id, source_label, title, url,
                    hn_url, score, comments, reading_time, tldr, points, why,
                    summarized, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    date, category, rank, c.dedup_id, c.source_id, c.source_label,
                    c.title, c.url, c.hn_url, c.score, c.comments, reading_time,
                    tldr, json.dumps(points, ensure_ascii=False), why,
                    1 if summarized else 0,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            self.conn.commit()

    def search(self, query: str, limit: int = 30) -> list[dict]:
        like = f"%{query}%"
        cur = self.conn.execute(
            f"""SELECT date, category, rank, title, url, source_label, tldr
                FROM items
                WHERE title LIKE ? OR tldr LIKE ? OR why LIKE ? OR points LIKE ?
                ORDER BY date DESC, {_CATEGORY_SQL_ORDER}, rank
                LIMIT ?""",
            (like, like, like, like, limit),
        )
        return [dict(r) for r in cur.fetchall()]
