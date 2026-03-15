"""dashboard summary 테스트."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from aiohttp import web

from gwanjong_mcp.dashboard import get_summary, perform_approval_action


def _seed_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id TEXT, platform TEXT NOT NULL,
                post_url TEXT, action TEXT NOT NULL,
                content TEXT, topic TEXT, timestamp TEXT NOT NULL
            );
            CREATE TABLE seen_posts (
                post_url TEXT PRIMARY KEY, platform TEXT NOT NULL,
                first_seen TEXT NOT NULL, acted INTEGER DEFAULT 0
            );
            CREATE TABLE rate_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL, action TEXT NOT NULL,
                timestamp TEXT NOT NULL, status TEXT DEFAULT 'ok'
            );
            CREATE TABLE replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comment_id TEXT NOT NULL UNIQUE, platform TEXT NOT NULL,
                post_url TEXT NOT NULL, parent_comment_id TEXT,
                author TEXT NOT NULL, body TEXT NOT NULL,
                detected_at TEXT NOT NULL, responded INTEGER DEFAULT 0
            );
            CREATE TABLE scout_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                total_scanned INTEGER NOT NULL DEFAULT 0,
                opportunities_count INTEGER NOT NULL DEFAULT 0,
                degraded_platforms_json TEXT NOT NULL DEFAULT '[]',
                platform_errors_json TEXT NOT NULL DEFAULT '{}',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE approval_queue (
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
            );
        """)
        conn.execute(
            "INSERT INTO actions (platform, post_url, action, content, topic, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "devto",
                "https://dev.to/example/post",
                "comment",
                "hello",
                "MCP",
                "2026-03-13T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO seen_posts (post_url, platform, first_seen, acted) VALUES (?, ?, ?, ?)",
            ("https://dev.to/example/post", "devto", "2026-03-13T00:00:00+00:00", 1),
        )
        conn.execute(
            "INSERT INTO replies (comment_id, platform, post_url, author, body, detected_at, responded) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "c1",
                "devto",
                "https://dev.to/example/post",
                "alice",
                "reply",
                "2026-03-13T01:00:00+00:00",
                0,
            ),
        )
        conn.execute(
            """
            INSERT INTO scout_runs (
                topic, total_scanned, opportunities_count, degraded_platforms_json,
                platform_errors_json, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "MCP",
                12,
                3,
                '["twitter"]',
                '{"twitter":{"get_trending":"API down"}}',
                "2개 플랫폼에서 3건의 기회 발견 (1개 플랫폼 일부 요청 실패)",
                "2026-03-13T02:15:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO scout_runs (
                topic, total_scanned, opportunities_count, degraded_platforms_json,
                platform_errors_json, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Discourse",
                8,
                2,
                '["discourse"]',
                '{"discourse":{"get_trending":{"https://forum-a.example.com":"timeout"}}}',
                "1개 플랫폼에서 2건의 기회 발견 (discourse 일부 인스턴스 실패)",
                "2026-03-13T05:15:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO approval_queue (
                topic, platform, action, status, opportunity_id, post_id, post_url,
                title, content, context_json, opportunity_json, created_at, reviewed_at, executed_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "MCP",
                "devto",
                "comment",
                "pending",
                "opp_0",
                "post_123",
                "https://dev.to/example/post",
                "Need feedback",
                "generated",
                "{}",
                "{}",
                "2026-03-13T02:00:00+00:00",
                None,
                None,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO approval_queue (
                topic, platform, action, status, opportunity_id, post_id, post_url,
                title, content, context_json, opportunity_json, created_at, reviewed_at, executed_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "MCP",
                "devto",
                "comment",
                "posted",
                "opp_1",
                "post_456",
                "https://dev.to/example/posted",
                "Posted item",
                "generated",
                "{}",
                "{}",
                "2026-03-13T02:30:00+00:00",
                "2026-03-13T03:00:00+00:00",
                "2026-03-13T03:00:00+00:00",
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO approval_queue (
                topic, platform, action, status, opportunity_id, post_id, post_url,
                title, content, context_json, opportunity_json, created_at, reviewed_at, executed_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "MCP",
                "devto",
                "comment",
                "failed",
                "opp_2",
                "post_789",
                "https://dev.to/example/failed",
                "Failed item",
                "generated",
                "{}",
                "{}",
                "2026-03-13T04:00:00+00:00",
                "2026-03-13T04:10:00+00:00",
                "2026-03-13T04:10:00+00:00",
                "boom",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_dashboard_summary_includes_approval_queue(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "dashboard.db"
    _seed_db(db_path)
    monkeypatch.setattr("gwanjong_mcp.dashboard.DB_PATH", db_path)

    summary = get_summary()

    assert summary["totals"]["pending_approvals"] == 1
    assert summary["totals"]["posted_approvals"] == 1
    assert summary["approval_stats"]["pending"] == 1
    assert summary["approval_stats"]["posted"] == 1
    assert summary["approval_stats"]["failed"] == 1
    assert len(summary["pending_approvals"]) == 1
    assert len(summary["failed_approvals"]) == 1
    assert summary["pending_approvals"][0]["title"] == "Need feedback"
    assert "post_id" in summary["recent_activity"][0]
    assert summary["scout_health"]["total_runs"] == 2
    assert summary["scout_health"]["degraded_runs"] == 2
    assert summary["scout_health"]["latest"]["degraded_platforms"] == ["discourse"]
    assert (
        summary["recent_scout_runs"][0]["platform_errors"]["discourse"]["get_trending"][
            "https://forum-a.example.com"
        ]
        == "timeout"
    )


@pytest.mark.asyncio
async def test_perform_approval_action_reject(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "dashboard.db"
    _seed_db(db_path)
    monkeypatch.setattr("gwanjong_mcp.dashboard.DB_PATH", db_path)

    result = await perform_approval_action(1, "reject")
    assert result["queue_status"] == "rejected"


@pytest.mark.asyncio
async def test_perform_approval_action_reject_posted_fails(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "dashboard.db"
    _seed_db(db_path)
    monkeypatch.setattr("gwanjong_mcp.dashboard.DB_PATH", db_path)

    with pytest.raises(web.HTTPBadRequest):
        await perform_approval_action(2, "reject")


@pytest.mark.asyncio
async def test_perform_approval_action_approve(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "dashboard.db"
    _seed_db(db_path)
    monkeypatch.setattr("gwanjong_mcp.dashboard.DB_PATH", db_path)

    async def fake_execute_approved(self, item_id: int) -> dict:
        return {"id": item_id, "queue_status": "posted", "response": {"status": "posted"}}

    monkeypatch.setattr(
        "gwanjong_mcp.dashboard.ApprovalQueue.execute_approved", fake_execute_approved
    )

    result = await perform_approval_action(1, "approve")
    assert result["queue_status"] == "posted"


@pytest.mark.asyncio
async def test_perform_approval_action_retry(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "dashboard.db"
    _seed_db(db_path)
    monkeypatch.setattr("gwanjong_mcp.dashboard.DB_PATH", db_path)

    async def fake_retry_failed(self, item_id: int) -> dict:
        return {"id": item_id, "queue_status": "posted", "response": {"status": "posted"}}

    monkeypatch.setattr("gwanjong_mcp.dashboard.ApprovalQueue.retry_failed", fake_retry_failed)

    result = await perform_approval_action(3, "retry")
    assert result["queue_status"] == "posted"


@pytest.mark.asyncio
async def test_perform_approval_action_invalid(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "dashboard.db"
    _seed_db(db_path)
    monkeypatch.setattr("gwanjong_mcp.dashboard.DB_PATH", db_path)

    with pytest.raises(web.HTTPBadRequest):
        await perform_approval_action(1, "noop")
