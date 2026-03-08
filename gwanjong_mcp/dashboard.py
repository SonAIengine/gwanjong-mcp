"""대시보드 웹서버 — aiohttp 기반 단독 프로세스.

SQLite를 직접 읽어서 JSON API 제공. devhub/mcp-pipeline 의존성 없음.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
DB_PATH = Path(os.getenv("GWANJONG_DB_PATH", str(Path.home() / ".gwanjong" / "memory.db")))

DEFAULT_LIMITS = {
    "devto": {"comments": 3, "posts": 1},
    "bluesky": {"comments": 5, "posts": 2},
    "twitter": {"comments": 5, "posts": 2},
    "reddit": {"comments": 3, "posts": 0},
}
PLATFORMS = list(DEFAULT_LIMITS.keys())
COOLDOWN_MINUTES = 30


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    for ddl in [
        """CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id TEXT, platform TEXT NOT NULL,
            post_url TEXT, action TEXT NOT NULL,
            content TEXT, topic TEXT, timestamp TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS seen_posts (
            post_url TEXT PRIMARY KEY, platform TEXT NOT NULL,
            first_seen TEXT NOT NULL, acted INTEGER DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS rate_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL, action TEXT NOT NULL,
            timestamp TEXT NOT NULL, status TEXT DEFAULT 'ok'
        )""",
        """CREATE TABLE IF NOT EXISTS replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id TEXT NOT NULL UNIQUE, platform TEXT NOT NULL,
            post_url TEXT NOT NULL, parent_comment_id TEXT,
            author TEXT NOT NULL, body TEXT NOT NULL,
            detected_at TEXT NOT NULL, responded INTEGER DEFAULT 0
        )""",
    ]:
        conn.execute(ddl)
    conn.commit()
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
                r["action"]: r["cnt"] for r in conn.execute(
                    "SELECT action, COUNT(*) as cnt FROM actions WHERE platform=? AND timestamp>=? GROUP BY action",
                    (p, week_ago),
                ).fetchall()
            }
            reply_cnt = conn.execute(
                "SELECT COUNT(*) FROM replies WHERE platform=?", (p,)
            ).fetchone()[0]
            platforms.append({
                "platform": p, "today": today_cnt, "week": week_cnt,
                "total": total_cnt, "actions_week": actions_week,
                "replies_received": reply_cnt,
            })

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
                    cooldown_remaining = max(0, COOLDOWN_MINUTES - elapsed)
                except (ValueError, TypeError):
                    pass

            rate_limits.append({
                "platform": p,
                "comments": {"used": c_used, "max": limits["comments"]},
                "posts": {"used": p_used, "max": limits["posts"]},
                "last_action": last_ts,
                "cooldown_remaining_min": round(cooldown_remaining, 1),
                "in_cooldown": cooldown_remaining > 0,
            })

        # Pending replies
        pending_replies = [
            dict(r) for r in conn.execute(
                "SELECT * FROM replies WHERE responded=0 ORDER BY detected_at DESC LIMIT 20"
            ).fetchall()
        ]

        # Recent activity (content 포함)
        recent = [
            dict(r) for r in conn.execute(
                "SELECT * FROM actions ORDER BY id DESC LIMIT 30"
            ).fetchall()
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
            engagement.append({
                "platform": p,
                "comments_sent": comments_sent,
                "replies_received": replies_got,
                "reply_rate": rate,
            })

        # Totals
        totals = {
            "total_actions": conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0],
            "total_seen_posts": conn.execute("SELECT COUNT(*) FROM seen_posts").fetchone()[0],
            "total_replies": conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0],
            "pending_replies": conn.execute("SELECT COUNT(*) FROM replies WHERE responded=0").fetchone()[0],
        }

        return {
            "generated_at": now.isoformat(),
            "platforms": platforms,
            "rate_limits": rate_limits,
            "pending_replies": pending_replies,
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


async def handle_index(request: web.Request) -> web.Response:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return web.Response(text="index.html not found", status=404)
    return web.FileResponse(index_path)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/summary", handle_api_summary)
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
