"""Shared SQLite path and schema helpers."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("GWANJONG_DB_PATH", str(Path.home() / ".gwanjong" / "memory.db")))


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and row access enabled."""
    resolved = db_path or DB_PATH
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved))
    conn.row_factory = sqlite3.Row
    # WAL 모드: 동시 read/write 안정성 확보 (dashboard + daemon 병렬 접근)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_actions_tables(conn: sqlite3.Connection) -> None:
    """Ensure action history and seen-post tables exist and are up to date."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id TEXT,
            post_id TEXT,
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
    _ensure_column(conn, "actions", "post_id", "TEXT")
    _ensure_column(conn, "actions", "author", "TEXT")
    conn.commit()


def ensure_scout_runs_table(conn: sqlite3.Connection) -> None:
    """Ensure scout diagnostics table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scout_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            total_scanned INTEGER NOT NULL DEFAULT 0,
            opportunities_count INTEGER NOT NULL DEFAULT 0,
            degraded_platforms_json TEXT NOT NULL DEFAULT '[]',
            platform_errors_json TEXT NOT NULL DEFAULT '{}',
            summary TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def ensure_rate_log_table(conn: sqlite3.Connection) -> None:
    """Ensure rate-limit tracking table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            action TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT DEFAULT 'ok'
        )
    """)
    conn.commit()


def ensure_replies_table(conn: sqlite3.Connection) -> None:
    """Ensure reply tracking table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL,
            post_url TEXT NOT NULL,
            parent_comment_id TEXT,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            responded INTEGER DEFAULT 0
        )
    """)
    conn.commit()


def ensure_approval_queue_table(conn: sqlite3.Connection) -> None:
    """Ensure approval queue table exists and includes current columns."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS approval_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            platform TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            opportunity_id TEXT NOT NULL,
            post_id TEXT NOT NULL,
            post_url TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            context_json TEXT NOT NULL,
            opportunity_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            executed_at TEXT,
            last_error TEXT
        )
    """)
    _ensure_column(conn, "approval_queue", "executed_at", "TEXT")
    _ensure_column(conn, "approval_queue", "last_error", "TEXT")
    conn.commit()


def ensure_campaigns_table(conn: sqlite3.Connection) -> None:
    """Ensure campaigns table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            objective TEXT NOT NULL DEFAULT 'awareness',
            topics_json TEXT NOT NULL DEFAULT '[]',
            platforms_json TEXT NOT NULL DEFAULT '[]',
            icp TEXT NOT NULL DEFAULT '',
            cta TEXT NOT NULL DEFAULT '',
            kpi_target_json TEXT NOT NULL DEFAULT '{}',
            start_date TEXT NOT NULL DEFAULT '',
            end_date TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        )
    """)
    _ensure_column(conn, "actions", "campaign_id", "TEXT")
    _ensure_column(conn, "actions", "utm_url", "TEXT")
    _ensure_column(conn, "actions", "agent_id", "TEXT")
    conn.commit()


def ensure_conversions_table(conn: sqlite3.Connection) -> None:
    """Ensure conversions table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversions (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            source TEXT NOT NULL,
            medium TEXT NOT NULL,
            action_id INTEGER,
            url TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL DEFAULT 'click',
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def ensure_assets_table(conn: sqlite3.Connection) -> None:
    """Ensure assets table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY,
            campaign_id TEXT,
            asset_type TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            usage_count INTEGER NOT NULL DEFAULT 0,
            last_used TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    _ensure_column(conn, "actions", "asset_id", "TEXT")
    conn.commit()


def ensure_message_frames_table(conn: sqlite3.Connection) -> None:
    """Ensure message_frames table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_frames (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            persona_segment TEXT NOT NULL,
            value_prop TEXT NOT NULL DEFAULT '',
            proof_points_json TEXT NOT NULL DEFAULT '[]',
            objections_json TEXT NOT NULL DEFAULT '{}',
            hooks_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def ensure_schedule_table(conn: sqlite3.Connection) -> None:
    """Ensure schedule table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            action TEXT NOT NULL,
            content TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            asset_ids_json TEXT NOT NULL DEFAULT '[]',
            published_at TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def ensure_agents_table(conn: sqlite3.Connection) -> None:
    """Ensure agents (character profiles) table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            avatar_style TEXT NOT NULL DEFAULT 'bottts',
            avatar_seed TEXT NOT NULL DEFAULT '',
            personality TEXT NOT NULL DEFAULT '',
            topics_json TEXT NOT NULL DEFAULT '[]',
            platforms_json TEXT NOT NULL DEFAULT '[]',
            tone TEXT NOT NULL DEFAULT 'casual-professional',
            max_length INTEGER NOT NULL DEFAULT 500,
            require_approval INTEGER NOT NULL DEFAULT 1,
            dry_run INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'idle',
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def ensure_experiments_table(conn: sqlite3.Connection) -> None:
    """Ensure A/B experiments table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS experiments (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            name TEXT NOT NULL,
            variant_a TEXT NOT NULL,
            variant_b TEXT NOT NULL,
            metric TEXT NOT NULL DEFAULT 'reply_rate',
            status TEXT NOT NULL DEFAULT 'running',
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def ensure_indexes(conn: sqlite3.Connection) -> None:
    """Create performance indexes for frequently queried columns."""
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_actions_platform_ts
            ON actions(platform, timestamp);
        CREATE INDEX IF NOT EXISTS idx_actions_post_url
            ON actions(post_url);
        CREATE INDEX IF NOT EXISTS idx_actions_agent_id
            ON actions(agent_id);
        CREATE INDEX IF NOT EXISTS idx_actions_author
            ON actions(author);
        CREATE INDEX IF NOT EXISTS idx_seen_posts_platform
            ON seen_posts(platform, acted);
        CREATE INDEX IF NOT EXISTS idx_rate_log_platform_action_ts
            ON rate_log(platform, action, timestamp);
        CREATE INDEX IF NOT EXISTS idx_rate_log_platform_status
            ON rate_log(platform, status);
        CREATE INDEX IF NOT EXISTS idx_approval_queue_status
            ON approval_queue(status);
        CREATE INDEX IF NOT EXISTS idx_replies_responded
            ON replies(responded);
        CREATE INDEX IF NOT EXISTS idx_scout_runs_created
            ON scout_runs(created_at);
    """)
    conn.commit()


def _ensure_column(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not rows:
        return  # 테이블이 없으면 건너뛰기
    columns = {row["name"] for row in rows}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
