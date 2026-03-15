"""Persistent memory — SQLite-based action history + deduplication. EventBus plugin."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import Event, EventBus
from .storage import DB_PATH, ensure_actions_tables, ensure_scout_runs_table, get_db

logger = logging.getLogger(__name__)


def _normalize_url(url: str) -> str:
    """Remove fragment (#comments etc.) for consistent URL matching."""
    return url.split("#")[0] if url else ""


def _get_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """SQLite connection. Creates tables if they don't exist."""
    conn = get_db(db_path)
    ensure_actions_tables(conn)
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
        response = event.data.get("response", {})
        topic = event.data.get("topic", "")
        conn = _get_db(self._db_path)
        ensure_scout_runs_table(conn)
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
            conn.execute(
                """
                INSERT INTO scout_runs (
                    topic, total_scanned, opportunities_count, degraded_platforms_json,
                    platform_errors_json, summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic,
                    int(response.get("total_scanned", 0)),
                    len(opportunities),
                    json.dumps(response.get("degraded_platforms", []), ensure_ascii=True),
                    json.dumps(response.get("platform_errors", {}), ensure_ascii=True),
                    str(response.get("summary", "")),
                    now,
                ),
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
            platform = (
                record.platform if hasattr(record, "platform") else record.get("platform", "")
            )

            # URL이 비어있으면 post_id로 자동 생성
            if not url:
                pid = record.post_id if hasattr(record, "post_id") else record.get("post_id", "")
                if pid and platform == "twitter":
                    url = f"https://x.com/i/status/{pid}"
            action = record.action if hasattr(record, "action") else record.get("action", "")
            opp_id = (
                record.opportunity_id
                if hasattr(record, "opportunity_id")
                else record.get("opportunity_id", "")
            )
            post_id = record.post_id if hasattr(record, "post_id") else record.get("post_id", "")
            timestamp = (
                record.timestamp if hasattr(record, "timestamp") else record.get("timestamp", "")
            )

            content = event.data.get("content", "")
            campaign_id = event.data.get("campaign_id", "")
            utm_url = event.data.get("utm_url", "")
            agent_id = os.getenv("GWANJONG_AGENT_ID", "") or None

            conn.execute(
                "INSERT INTO actions (opportunity_id, post_id, platform, post_url, action, content, timestamp, campaign_id, utm_url, agent_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    opp_id,
                    post_id,
                    platform,
                    url,
                    action,
                    content,
                    timestamp,
                    campaign_id or None,
                    utm_url or None,
                    agent_id,
                ),
            )
            # seen_posts에 활동 표시 (URL 정규화 — #comments 등 제거)
            norm_url = _normalize_url(url)
            if norm_url:
                conn.execute(
                    "UPDATE seen_posts SET acted = 1 WHERE post_url = ? OR post_url = ?",
                    (url, norm_url),
                )
                # seen_posts에 없으면 새로 추가
                conn.execute(
                    "INSERT OR IGNORE INTO seen_posts (post_url, platform, first_seen, acted) VALUES (?, ?, ?, 1)",
                    (norm_url, platform, timestamp),
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
                norm = _normalize_url(url)
                row = conn.execute(
                    "SELECT acted FROM seen_posts WHERE (post_url = ? OR post_url = ?) AND acted = 1",
                    (url, norm),
                ).fetchone()
                if not row:
                    filtered[opp_id] = opp
            return filtered
        finally:
            conn.close()
