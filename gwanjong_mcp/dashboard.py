"""Dashboard web server — standalone process based on aiohttp.

Reads SQLite directly to serve JSON APIs. No devhub/mcp-pipeline dependency.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web

from .approval import ApprovalQueue
from .policy import DEFAULT_LIMITS, PLATFORMS
from .storage import (
    DB_PATH as _DEFAULT_DB_PATH,
)
from .storage import (
    ensure_actions_tables,
    ensure_approval_queue_table,
    ensure_campaigns_table,
    ensure_conversions_table,
    ensure_rate_log_table,
    ensure_replies_table,
    ensure_schedule_table,
    ensure_scout_runs_table,
    get_db,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
DB_PATH = Path(_DEFAULT_DB_PATH)


def _get_db() -> sqlite3.Connection:
    conn = get_db(DB_PATH)
    ensure_actions_tables(conn)
    ensure_rate_log_table(conn)
    ensure_replies_table(conn)
    ensure_scout_runs_table(conn)
    ensure_approval_queue_table(conn)
    ensure_campaigns_table(conn)
    ensure_conversions_table(conn)
    ensure_schedule_table(conn)
    return conn


def get_summary() -> dict:
    conn = _get_db()
    try:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        # Platform stats
        platforms = []
        for p in PLATFORMS:
            today_cnt = conn.execute(
                "SELECT COUNT(*) FROM actions WHERE platform=? AND timestamp>=?", (p, today)
            ).fetchone()[0]
            week_cnt = conn.execute(
                "SELECT COUNT(*) FROM actions WHERE platform=? AND timestamp>=?", (p, week_ago)
            ).fetchone()[0]
            total_cnt = conn.execute(
                "SELECT COUNT(*) FROM actions WHERE platform=?", (p,)
            ).fetchone()[0]
            actions_week = {
                r["action"]: r["cnt"]
                for r in conn.execute(
                    "SELECT action, COUNT(*) as cnt FROM actions WHERE platform=? AND timestamp>=? GROUP BY action",
                    (p, week_ago),
                ).fetchall()
            }
            reply_cnt = conn.execute(
                "SELECT COUNT(*) FROM replies WHERE platform=?", (p,)
            ).fetchone()[0]
            platforms.append(
                {
                    "platform": p,
                    "today": today_cnt,
                    "week": week_cnt,
                    "total": total_cnt,
                    "actions_week": actions_week,
                    "replies_received": reply_cnt,
                }
            )

        # Rate limits + cooldown
        rate_limits = []
        for p, limits in DEFAULT_LIMITS.items():
            c_used = conn.execute(
                "SELECT COUNT(*) FROM rate_log WHERE platform=? AND action='comment' AND timestamp>=? AND status='ok'",
                (p, today),
            ).fetchone()[0]
            p_used = conn.execute(
                "SELECT COUNT(*) FROM rate_log WHERE platform=? AND action='post' AND timestamp>=? AND status='ok'",
                (p, today),
            ).fetchone()[0]
            last = conn.execute(
                "SELECT timestamp FROM rate_log WHERE platform=? AND status='ok' ORDER BY id DESC LIMIT 1",
                (p,),
            ).fetchone()
            last_ts = last["timestamp"] if last else None

            # 쿨다운 계산
            cooldown_remaining = 0
            if last_ts:
                try:
                    last_dt = datetime.fromisoformat(last_ts)
                    elapsed = (now - last_dt).total_seconds() / 60
                    cooldown_remaining = max(0, limits.min_interval_minutes - elapsed)
                except (ValueError, TypeError):
                    pass

            rate_limits.append(
                {
                    "platform": p,
                    "comments": {"used": c_used, "max": limits.max_comments_per_day},
                    "posts": {"used": p_used, "max": limits.max_posts_per_day},
                    "last_action": last_ts,
                    "cooldown_remaining_min": round(cooldown_remaining, 1),
                    "in_cooldown": cooldown_remaining > 0,
                }
            )

        # Pending replies
        pending_replies = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM replies WHERE responded=0 ORDER BY detected_at DESC LIMIT 20"
            ).fetchall()
        ]
        scout_runs = [
            {
                "topic": row["topic"],
                "total_scanned": row["total_scanned"],
                "opportunities_count": row["opportunities_count"],
                "degraded_platforms": json.loads(row["degraded_platforms_json"]),
                "platform_errors": json.loads(row["platform_errors_json"]),
                "summary": row["summary"],
                "created_at": row["created_at"],
            }
            for row in conn.execute(
                """
                SELECT topic, total_scanned, opportunities_count, degraded_platforms_json,
                       platform_errors_json, summary, created_at
                FROM scout_runs
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()
        ]
        scout_health = {
            "total_runs": conn.execute("SELECT COUNT(*) FROM scout_runs").fetchone()[0],
            "degraded_runs": conn.execute(
                "SELECT COUNT(*) FROM scout_runs WHERE degraded_platforms_json != '[]'"
            ).fetchone()[0],
            "latest": scout_runs[0] if scout_runs else None,
        }

        pending_approvals = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, topic, platform, action, status, title, post_url, created_at
                FROM approval_queue
                WHERE status='pending'
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()
        ]

        failed_approvals = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, topic, platform, action, status, title, post_url, created_at, last_error
                FROM approval_queue
                WHERE status='failed'
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()
        ]

        approval_stats_row = {
            r["status"]: r["cnt"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM approval_queue GROUP BY status"
            ).fetchall()
        }

        # Recent activity (content 포함)
        recent = [
            dict(r)
            for r in conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT 30").fetchall()
        ]

        # Weekly chart — 플랫폼별 분리
        chart = []
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            day_data: dict = {"date": day, "total": 0}
            for p in PLATFORMS:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM actions WHERE platform=? AND timestamp>=? AND timestamp<date(?,'+1 day')",
                    (p, day, day),
                ).fetchone()[0]
                day_data[p] = cnt
                day_data["total"] += cnt
            chart.append(day_data)

        # Engagement — 플랫폼별 댓글 대비 답글 비율
        engagement = []
        for p in PLATFORMS:
            comments_sent = conn.execute(
                "SELECT COUNT(*) FROM actions WHERE platform=? AND action='comment'", (p,)
            ).fetchone()[0]
            replies_got = conn.execute(
                "SELECT COUNT(*) FROM replies WHERE platform=?", (p,)
            ).fetchone()[0]
            rate = round(replies_got / comments_sent * 100, 1) if comments_sent > 0 else 0
            engagement.append(
                {
                    "platform": p,
                    "comments_sent": comments_sent,
                    "replies_received": replies_got,
                    "reply_rate": rate,
                }
            )

        # Totals
        totals = {
            "total_actions": conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0],
            "total_seen_posts": conn.execute("SELECT COUNT(*) FROM seen_posts").fetchone()[0],
            "total_replies": conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0],
            "pending_replies": conn.execute(
                "SELECT COUNT(*) FROM replies WHERE responded=0"
            ).fetchone()[0],
            "pending_approvals": approval_stats_row.get("pending", 0),
            "posted_approvals": approval_stats_row.get("posted", 0),
        }

        return {
            "generated_at": now.isoformat(),
            "platforms": platforms,
            "rate_limits": rate_limits,
            "scout_health": scout_health,
            "recent_scout_runs": scout_runs,
            "pending_replies": pending_replies,
            "pending_approvals": pending_approvals,
            "failed_approvals": failed_approvals,
            "approval_stats": {
                "pending": approval_stats_row.get("pending", 0),
                "approved": approval_stats_row.get("approved", 0),
                "rejected": approval_stats_row.get("rejected", 0),
                "executing": approval_stats_row.get("executing", 0),
                "posted": approval_stats_row.get("posted", 0),
                "failed": approval_stats_row.get("failed", 0),
            },
            "recent_activity": recent,
            "weekly_chart": chart,
            "engagement": engagement,
            "totals": totals,
        }
    finally:
        conn.close()


async def handle_api_summary(request: web.Request) -> web.Response:
    data = get_summary()
    return web.json_response(data)


async def perform_approval_action(item_id: int, action: str) -> dict:
    queue = ApprovalQueue(db_path=DB_PATH)

    try:
        if action == "approve":
            return await queue.execute_approved(item_id)
        if action == "retry":
            return await queue.retry_failed(item_id)
        if action == "reject":
            item = queue.get_item(item_id)
            if item is None:
                raise web.HTTPNotFound(text=f"approval item not found: {item_id}")
            queue.mark_rejected(item_id)
            return {"id": item_id, "queue_status": "rejected"}
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc

    raise web.HTTPBadRequest(text=f"unsupported action: {action}")


async def handle_api_approval_action(request: web.Request) -> web.Response:
    item_id = int(request.match_info["item_id"])
    action = request.match_info["action"]
    result = await perform_approval_action(item_id, action)
    return web.json_response(result)


async def handle_api_campaigns(request: web.Request) -> web.Response:
    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()
        campaigns = [dict(r) for r in rows]
        return web.json_response({"campaigns": campaigns, "count": len(campaigns)})
    finally:
        conn.close()


async def handle_api_conversions(request: web.Request) -> web.Response:
    campaign_id = request.query.get("campaign_id", "")
    conn = _get_db()
    try:
        if campaign_id:
            rows = conn.execute(
                "SELECT * FROM conversions WHERE campaign_id = ? ORDER BY created_at DESC LIMIT 100",
                (campaign_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM conversions ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        return web.json_response({"conversions": [dict(r) for r in rows], "count": len(rows)})
    finally:
        conn.close()


async def handle_api_schedule(request: web.Request) -> web.Response:
    campaign_id = request.query.get("campaign_id", "")
    conn = _get_db()
    try:
        if campaign_id:
            rows = conn.execute(
                "SELECT * FROM schedule WHERE campaign_id = ? ORDER BY scheduled_at DESC LIMIT 100",
                (campaign_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM schedule ORDER BY scheduled_at DESC LIMIT 100"
            ).fetchall()
        return web.json_response({"schedule": [dict(r) for r in rows], "count": len(rows)})
    finally:
        conn.close()


async def handle_index(request: web.Request) -> web.Response:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return web.Response(text="index.html not found", status=404)
    return web.FileResponse(index_path)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/summary", handle_api_summary)
    app.router.add_get("/api/campaigns", handle_api_campaigns)
    app.router.add_get("/api/conversions", handle_api_conversions)
    app.router.add_get("/api/schedule", handle_api_schedule)
    app.router.add_post("/api/approvals/{item_id:\\d+}/{action}", handle_api_approval_action)
    if STATIC_DIR.exists():
        app.router.add_static("/static/", STATIC_DIR)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="gwanjong dashboard")
    parser.add_argument("--port", type=int, default=8585)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info("DB: %s", DB_PATH)
    logger.info("Dashboard: http://%s:%d", args.host, args.port)
    web.run_app(create_app(), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
