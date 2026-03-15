"""Monitoring data aggregation — generate comprehensive reports from SQLite. No web dependencies."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .policy import DEFAULT_LIMITS, PLATFORMS
from .storage import (
    DB_PATH as _DEFAULT_DB_PATH,
)
from .storage import (
    ensure_actions_tables,
    ensure_rate_log_table,
    ensure_replies_table,
    ensure_scout_runs_table,
    get_db,
)

DB_PATH = Path(os.getenv("GWANJONG_DB_PATH", str(_DEFAULT_DB_PATH)))


def get_summary(db_path: Path = DB_PATH) -> dict[str, Any]:
    """Collect all dashboard data in a single call."""
    conn = get_db(db_path)
    ensure_actions_tables(conn)
    ensure_rate_log_table(conn)
    ensure_replies_table(conn)
    ensure_scout_runs_table(conn)
    try:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "platforms": _platform_stats(conn),
            "rate_limits": _rate_limit_status(conn),
            "scout_health": _scout_health(conn),
            "recent_scout_runs": _recent_scout_runs(conn),
            "pending_replies": _pending_replies(conn),
            "recent_activity": _recent_activity(conn, limit=30),
            "weekly_chart": _weekly_chart(conn),
            "totals": _totals(conn),
        }
    finally:
        conn.close()


def _platform_stats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per-platform activity statistics."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    stats = []
    for p in PLATFORMS:
        # 오늘 활동
        today_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM actions WHERE platform = ? AND timestamp >= ?",
            (p, today),
        ).fetchone()
        # 주간 활동
        week_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM actions WHERE platform = ? AND timestamp >= ?",
            (p, week_ago),
        ).fetchone()
        # 전체 활동
        total_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM actions WHERE platform = ?",
            (p,),
        ).fetchone()
        # 액션별 분류 (주간)
        action_rows = conn.execute(
            "SELECT action, COUNT(*) as cnt FROM actions WHERE platform = ? AND timestamp >= ? GROUP BY action",
            (p, week_ago),
        ).fetchall()
        # 받은 답글 수
        reply_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM replies WHERE platform = ?",
            (p,),
        ).fetchone()

        stats.append(
            {
                "platform": p,
                "today": today_row[0] if today_row else 0,
                "week": week_row[0] if week_row else 0,
                "total": total_row[0] if total_row else 0,
                "actions_week": {r[0]: r[1] for r in action_rows},
                "replies_received": reply_row[0] if reply_row else 0,
            }
        )
    return stats


def _rate_limit_status(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per-platform rate limit remaining quota."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    limits = []
    for p, limit in DEFAULT_LIMITS.items():
        # 오늘 action별 사용량
        comment_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM rate_log WHERE platform = ? AND action = 'comment' AND timestamp >= ? AND status = 'ok'",
            (p, today),
        ).fetchone()
        post_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM rate_log WHERE platform = ? AND action = 'post' AND timestamp >= ? AND status = 'ok'",
            (p, today),
        ).fetchone()
        # 마지막 활동 시각
        last_row = conn.execute(
            "SELECT timestamp FROM rate_log WHERE platform = ? AND status = 'ok' ORDER BY id DESC LIMIT 1",
            (p,),
        ).fetchone()

        comments_used = comment_row[0] if comment_row else 0
        posts_used = post_row[0] if post_row else 0

        limits.append(
            {
                "platform": p,
                "comments": {"used": comments_used, "max": limit.max_comments_per_day},
                "posts": {"used": posts_used, "max": limit.max_posts_per_day},
                "cooldown_minutes": limit.min_interval_minutes,
                "last_action": last_row[0] if last_row else None,
            }
        )
    return limits


def _recent_scout_runs(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT topic, total_scanned, opportunities_count, degraded_platforms_json,
               platform_errors_json, summary, created_at
        FROM scout_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "topic": row["topic"],
            "total_scanned": row["total_scanned"],
            "opportunities_count": row["opportunities_count"],
            "degraded_platforms": json.loads(row["degraded_platforms_json"]),
            "platform_errors": json.loads(row["platform_errors_json"]),
            "summary": row["summary"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _scout_health(conn: sqlite3.Connection) -> dict[str, Any]:
    recent = _recent_scout_runs(conn, limit=10)
    total_runs = conn.execute("SELECT COUNT(*) FROM scout_runs").fetchone()[0]
    degraded_runs = conn.execute(
        "SELECT COUNT(*) FROM scout_runs WHERE degraded_platforms_json != '[]'"
    ).fetchone()[0]
    return {
        "total_runs": total_runs,
        "degraded_runs": degraded_runs,
        "latest": recent[0] if recent else None,
    }


def _pending_replies(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """List of unanswered replies."""
    rows = conn.execute(
        "SELECT * FROM replies WHERE responded = 0 ORDER BY detected_at DESC LIMIT 20",
    ).fetchall()
    return [
        {
            "id": r[0],
            "comment_id": r[1],
            "platform": r[2],
            "post_url": r[3],
            "author": r[5],
            "body": r[6],
            "detected_at": r[7],
        }
        for r in rows
    ]


def _recent_activity(conn: sqlite3.Connection, limit: int = 30) -> list[dict[str, Any]]:
    """Recent activity timeline."""
    rows = conn.execute(
        "SELECT * FROM actions ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _weekly_chart(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Daily activity counts for the last 7 days."""
    now = datetime.now(timezone.utc)
    chart = []
    for i in range(6, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM actions WHERE timestamp >= ? AND timestamp < date(?, '+1 day')",
            (day, day),
        ).fetchone()
        chart.append({"date": day, "count": row[0] if row else 0})
    return chart


def _totals(conn: sqlite3.Connection) -> dict[str, Any]:
    """Overall totals."""
    actions = conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
    seen = conn.execute("SELECT COUNT(*) FROM seen_posts").fetchone()[0]
    replies = conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM replies WHERE responded = 0").fetchone()[0]
    return {
        "total_actions": actions,
        "total_seen_posts": seen,
        "total_replies": replies,
        "pending_replies": pending,
    }
