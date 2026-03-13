"""Persistent memory — SQLite-based action history + deduplication. EventBus plugin."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import Event, EventBus

logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("GWANJONG_DB_PATH", str(Path.home() / ".gwanjong" / "memory.db")))


def _get_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """SQLite connection. Creates tables if they don't exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id TEXT,
            platform TEXT NOT NULL,
            post_url TEXT,
            action TEXT NOT NULL,
            content TEXT,
            topic TEXT,
            timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS seen_posts (
            post_url TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            acted INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    return conn


class Memory:
    """Persist action history + prevent duplicates. Auto-records when attached to EventBus."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path

    def attach(self, bus: EventBus) -> None:
        """Attach to EventBus."""
        bus.on("scout.done", self._on_scout_done)
        bus.on("strike.after", self._on_strike_after)
        logger.info("Memory attached to EventBus")

    async def _on_scout_done(self, event: Event) -> None:
        """Record discovered posts in seen_posts when scout completes."""
        opportunities = event.data.get("opportunities", {})
        conn = _get_db(self._db_path)
        try:
            now = datetime.now(timezone.utc).isoformat()
            for opp in opportunities.values():
                url = opp.url if hasattr(opp, "url") else opp.get("url", "")
                platform = opp.platform if hasattr(opp, "platform") else opp.get("platform", "")
                if url:
                    conn.execute(
                        "INSERT OR IGNORE INTO seen_posts (post_url, platform, first_seen) VALUES (?, ?, ?)",
                        (url, platform, now),
                    )
            conn.commit()
        finally:
            conn.close()

    async def _on_strike_after(self, event: Event) -> None:
        """Record action history when strike completes."""
        record = event.data.get("record")
        if record is None:
            return

        conn = _get_db(self._db_path)
        try:
            url = record.url if hasattr(record, "url") else record.get("url", "")
            platform = record.platform if hasattr(record, "platform") else record.get("platform", "")
            action = record.action if hasattr(record, "action") else record.get("action", "")
            opp_id = record.opportunity_id if hasattr(record, "opportunity_id") else record.get("opportunity_id", "")
            timestamp = record.timestamp if hasattr(record, "timestamp") else record.get("timestamp", "")

            content = event.data.get("content", "")

            conn.execute(
                "INSERT INTO actions (opportunity_id, platform, post_url, action, content, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                (opp_id, platform, url, action, content, timestamp),
            )
            # seen_posts에 활동 표시
            if url:
                conn.execute(
                    "UPDATE seen_posts SET acted = 1 WHERE post_url = ?",
                    (url,),
                )
            conn.commit()
        finally:
            conn.close()

    # ── 조회 API ──

    def is_acted(self, post_url: str) -> bool:
        """Check if a post has already been acted on."""
        conn = _get_db(self._db_path)
        try:
            row = conn.execute(
                "SELECT acted FROM seen_posts WHERE post_url = ?",
                (post_url,),
            ).fetchone()
            return bool(row and row["acted"])
        finally:
            conn.close()

    def get_history(self, limit: int = 20, platform: str | None = None) -> list[dict[str, Any]]:
        """Retrieve recent action history."""
        conn = _get_db(self._db_path)
        try:
            if platform:
                rows = conn.execute(
                    "SELECT * FROM actions WHERE platform = ? ORDER BY id DESC LIMIT ?",
                    (platform, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM actions ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_action_count(self, platform: str | None = None, days: int = 7) -> int:
        """Get action count for the last N days."""
        conn = _get_db(self._db_path)
        try:
            cutoff = datetime.now(timezone.utc).isoformat()[:10]  # 간단 비교용
            if platform:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM actions WHERE platform = ? AND timestamp >= date(?, '-' || ? || ' days')",
                    (platform, cutoff, days),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM actions WHERE timestamp >= date(?, '-' || ? || ' days')",
                    (cutoff, days),
                ).fetchone()
            return row["cnt"] if row else 0
        finally:
            conn.close()

    def filter_unseen(self, opportunities: dict[str, Any]) -> dict[str, Any]:
        """Filter out opportunities that have already been acted on."""
        conn = _get_db(self._db_path)
        try:
            filtered = {}
            for opp_id, opp in opportunities.items():
                url = opp.url if hasattr(opp, "url") else opp.get("url", "")
                row = conn.execute(
                    "SELECT acted FROM seen_posts WHERE post_url = ? AND acted = 1",
                    (url,),
                ).fetchone()
                if not row:
                    filtered[opp_id] = opp
            return filtered
        finally:
            conn.close()
