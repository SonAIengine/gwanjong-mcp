"""Dashboard web server — standalone process based on aiohttp.

Reads SQLite directly to serve JSON APIs. No devhub/mcp-pipeline dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web

from .approval import ApprovalQueue
from .campaign import CampaignManager
from .policy import DEFAULT_LIMITS, PLATFORMS
from .storage import (
    DB_PATH as _DEFAULT_DB_PATH,
)
from .storage import (
    ensure_actions_tables,
    ensure_agents_table,
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

# ── Daemon process manager ──

_daemon_proc: asyncio.subprocess.Process | None = None
_daemon_log: list[str] = []
_daemon_config: dict = {}
_DAEMON_LOG_MAX = 200


async def _read_daemon_output(stream: asyncio.StreamReader, label: str) -> None:
    """Read daemon stdout/stderr and append to log buffer."""
    global _daemon_log
    while True:
        line = await stream.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip()
        _daemon_log.append(f"[{label}] {text}")
        if len(_daemon_log) > _DAEMON_LOG_MAX:
            _daemon_log = _daemon_log[-_DAEMON_LOG_MAX:]
        logger.debug("daemon %s: %s", label, text)


async def daemon_start(config: dict) -> dict:
    """Start gwanjong-daemon as a subprocess."""
    global _daemon_proc, _daemon_config, _daemon_log

    if _daemon_proc and _daemon_proc.returncode is None:
        return {"error": "daemon already running", "pid": _daemon_proc.pid}

    cmd = [sys.executable, "-m", "gwanjong_mcp.daemon"]
    topics = config.get("topics", "MCP")
    cmd.extend(["--topics", topics])

    if config.get("platforms"):
        cmd.extend(["--platforms", config["platforms"]])
    if config.get("interval"):
        cmd.extend(["--interval", str(config["interval"])])
    if config.get("max_actions"):
        cmd.extend(["--max-actions", str(config["max_actions"])])
    if config.get("max_cycles"):
        cmd.extend(["--max-cycles", str(config["max_cycles"])])
    if config.get("campaign"):
        cmd.extend(["--campaign", config["campaign"]])
    if config.get("require_approval"):
        cmd.append("--require-approval")
    if config.get("dry_run"):
        cmd.append("--dry-run")
    if config.get("auto_plan"):
        cmd.append("--auto-plan")

    _daemon_log = [f"[sys] Starting: {' '.join(cmd)}"]
    _daemon_config = config

    _daemon_proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    # Background tasks to read output
    asyncio.create_task(_read_daemon_output(_daemon_proc.stdout, "out"))
    asyncio.create_task(_read_daemon_output(_daemon_proc.stderr, "err"))

    _daemon_log.append(f"[sys] Daemon started (PID {_daemon_proc.pid})")
    logger.info("Daemon started: PID %d, cmd=%s", _daemon_proc.pid, cmd)
    return {"status": "started", "pid": _daemon_proc.pid}


async def daemon_stop() -> dict:
    """Stop the running daemon subprocess."""
    global _daemon_proc
    if not _daemon_proc or _daemon_proc.returncode is not None:
        return {"status": "not_running"}

    pid = _daemon_proc.pid
    _daemon_proc.terminate()
    try:
        await asyncio.wait_for(_daemon_proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        _daemon_proc.kill()
        await _daemon_proc.wait()

    _daemon_log.append(f"[sys] Daemon stopped (PID {pid})")
    logger.info("Daemon stopped: PID %d", pid)
    _daemon_proc = None
    return {"status": "stopped", "pid": pid}


def daemon_status() -> dict:
    """Get daemon process status."""
    if _daemon_proc and _daemon_proc.returncode is None:
        return {
            "running": True,
            "pid": _daemon_proc.pid,
            "config": _daemon_config,
            "log_lines": len(_daemon_log),
        }
    return {
        "running": False,
        "pid": None,
        "config": _daemon_config,
        "exit_code": _daemon_proc.returncode if _daemon_proc else None,
        "log_lines": len(_daemon_log),
    }


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
    ensure_agents_table(conn)
    return conn


# ── Agent (character) management ──

_agent_daemons: dict[str, asyncio.subprocess.Process] = {}
_agent_logs: dict[str, list[str]] = {}


async def _read_agent_output(agent_id: str, stream: asyncio.StreamReader, label: str) -> None:
    logs = _agent_logs.setdefault(agent_id, [])
    while True:
        line = await stream.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip()
        logs.append(f"[{label}] {text}")
        if len(logs) > _DAEMON_LOG_MAX:
            _agent_logs[agent_id] = logs[-_DAEMON_LOG_MAX:]


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
                SELECT id, topic, platform, action, status, title, post_url, content, created_at
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
                SELECT id, topic, platform, action, status, title, post_url, content, created_at, last_error
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
    from .events import Blocked

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
    except Blocked as exc:
        return {"id": item_id, "queue_status": "failed", "error": str(exc)}
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


# ── Daemon API handlers ──


async def handle_api_daemon_start(request: web.Request) -> web.Response:
    try:
        config = await request.json()
    except Exception:
        config = {}
    result = await daemon_start(config)
    status = 200 if "error" not in result else 409
    return web.json_response(result, status=status)


async def handle_api_daemon_stop(request: web.Request) -> web.Response:
    result = await daemon_stop()
    return web.json_response(result)


async def handle_api_daemon_status(request: web.Request) -> web.Response:
    return web.json_response(daemon_status())


async def handle_api_daemon_logs(request: web.Request) -> web.Response:
    offset = int(request.query.get("offset", "0"))
    limit = int(request.query.get("limit", "100"))
    logs = _daemon_log[offset : offset + limit]
    return web.json_response({"logs": logs, "total": len(_daemon_log), "offset": offset})


# ── Campaign API handlers ──


async def handle_api_campaign_report(request: web.Request) -> web.Response:
    campaign_id = request.match_info["campaign_id"]
    mgr = CampaignManager(db_path=DB_PATH)
    report = mgr.get_report(campaign_id)
    return web.json_response(report)


async def handle_api_campaign_create(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if "name" not in data:
        return web.json_response({"error": "name 필수"}, status=400)
    mgr = CampaignManager(db_path=DB_PATH)
    camp = mgr.create(data)
    return web.json_response(
        {
            "id": camp.id,
            "name": camp.name,
            "status": camp.status,
            "objective": camp.objective,
        }
    )


async def handle_api_campaign_update(request: web.Request) -> web.Response:
    campaign_id = request.match_info["campaign_id"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    mgr = CampaignManager(db_path=DB_PATH)
    camp = mgr.update(campaign_id, data)
    if not camp:
        return web.json_response({"error": f"캠페인 '{campaign_id}' 없음"}, status=404)
    return web.json_response({"id": camp.id, "name": camp.name, "status": camp.status})


# ── Agents API handlers ──


async def handle_api_agents_list(request: web.Request) -> web.Response:
    conn = _get_db()
    try:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        rows = conn.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
        agents = []
        for r in rows:
            agent = dict(r)
            agent_id = agent["id"]
            proc = _agent_daemons.get(agent_id)
            agent["running"] = proc is not None and proc.returncode is None
            agent["pid"] = proc.pid if agent["running"] else None

            # 에이전트별 실적
            agent["stats"] = {
                "today": conn.execute(
                    "SELECT COUNT(*) FROM actions WHERE agent_id = ? AND timestamp >= ?",
                    (agent_id, today),
                ).fetchone()[0],
                "week": conn.execute(
                    "SELECT COUNT(*) FROM actions WHERE agent_id = ? AND timestamp >= ?",
                    (agent_id, week_ago),
                ).fetchone()[0],
                "total": conn.execute(
                    "SELECT COUNT(*) FROM actions WHERE agent_id = ?", (agent_id,)
                ).fetchone()[0],
                "pending": conn.execute(
                    "SELECT COUNT(*) FROM approval_queue WHERE status = 'pending'"
                ).fetchone()[0],
            }

            agents.append(agent)
        return web.json_response({"agents": agents, "count": len(agents)})
    finally:
        conn.close()


async def handle_api_agent_create(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if "name" not in data:
        return web.json_response({"error": "name 필수"}, status=400)

    import uuid

    agent_id = f"agent_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO agents (id, name, avatar_style, avatar_seed, personality,
                topics_json, platforms_json, tone, max_length, require_approval, dry_run, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?)""",
            (
                agent_id,
                data["name"],
                data.get("avatar_style", "bottts"),
                data.get("avatar_seed", data["name"]),
                data.get("personality", ""),
                json.dumps(data.get("topics", ["MCP"])),
                json.dumps(data.get("platforms", ["devto"])),
                data.get("tone", "casual-professional"),
                data.get("max_length", 500),
                1 if data.get("require_approval", True) else 0,
                1 if data.get("dry_run", False) else 0,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return web.json_response({"id": agent_id, "name": data["name"]})


async def handle_api_agent_update(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if not row:
            return web.json_response({"error": "에이전트 없음"}, status=404)

        updates, params = [], []
        field_map = {
            "name": "name",
            "avatar_style": "avatar_style",
            "avatar_seed": "avatar_seed",
            "personality": "personality",
            "tone": "tone",
            "max_length": "max_length",
            "require_approval": "require_approval",
            "dry_run": "dry_run",
        }
        json_fields = {"topics": "topics_json", "platforms": "platforms_json"}

        for k, col in field_map.items():
            if k in data:
                updates.append(f"{col} = ?")
                params.append(data[k])
        for k, col in json_fields.items():
            if k in data:
                updates.append(f"{col} = ?")
                params.append(json.dumps(data[k]))

        if updates:
            params.append(agent_id)
            conn.execute(f"UPDATE agents SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()

        return web.json_response({"id": agent_id, "updated": True})
    finally:
        conn.close()


async def handle_api_agent_delete(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    # 실행 중이면 먼저 중지
    proc = _agent_daemons.get(agent_id)
    if proc and proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
        _agent_daemons.pop(agent_id, None)

    conn = _get_db()
    try:
        conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        conn.commit()
    finally:
        conn.close()
    return web.json_response({"id": agent_id, "deleted": True})


async def handle_api_agent_start(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]

    proc = _agent_daemons.get(agent_id)
    if proc and proc.returncode is None:
        return web.json_response({"error": "이미 실행 중", "pid": proc.pid}, status=409)

    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if not row:
            return web.json_response({"error": "에이전트 없음"}, status=404)
        agent = dict(row)
    finally:
        conn.close()

    topics = json.loads(agent["topics_json"])
    platforms = json.loads(agent["platforms_json"])

    cmd = [sys.executable, "-m", "gwanjong_mcp.daemon"]
    cmd.extend(["--topics", ",".join(topics)])
    if platforms:
        cmd.extend(["--platforms", ",".join(platforms)])
    cmd.extend(["--max-actions", "3"])
    if agent.get("require_approval", 1):
        cmd.append("--require-approval")
    if agent.get("dry_run", 0):
        cmd.append("--dry-run")

    _agent_logs[agent_id] = [f"[sys] {agent['name']} 시작: {' '.join(cmd)}"]

    agent_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    agent_env["GWANJONG_AGENT_ID"] = agent_id
    if agent.get("personality"):
        agent_env["GWANJONG_AGENT_PERSONALITY"] = agent["personality"]
    if agent.get("name"):
        agent_env["GWANJONG_AGENT_NAME"] = agent["name"]
    if agent.get("tone"):
        agent_env["GWANJONG_AGENT_TONE"] = agent["tone"]

    new_proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=agent_env,
    )
    _agent_daemons[agent_id] = new_proc
    asyncio.create_task(_read_agent_output(agent_id, new_proc.stdout, "out"))
    asyncio.create_task(_read_agent_output(agent_id, new_proc.stderr, "err"))

    conn = _get_db()
    try:
        conn.execute("UPDATE agents SET status = 'running' WHERE id = ?", (agent_id,))
        conn.commit()
    finally:
        conn.close()

    _agent_logs[agent_id].append(f"[sys] PID {new_proc.pid}")
    return web.json_response({"id": agent_id, "status": "started", "pid": new_proc.pid})


async def handle_api_agent_stop(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    proc = _agent_daemons.get(agent_id)
    if not proc or proc.returncode is not None:
        return web.json_response({"status": "not_running"})

    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()

    _agent_daemons.pop(agent_id, None)
    logs = _agent_logs.get(agent_id, [])
    logs.append("[sys] 정지됨")

    conn = _get_db()
    try:
        conn.execute("UPDATE agents SET status = 'idle' WHERE id = ?", (agent_id,))
        conn.commit()
    finally:
        conn.close()

    return web.json_response({"id": agent_id, "status": "stopped"})


async def handle_api_agent_logs(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    logs = _agent_logs.get(agent_id, [])
    return web.json_response({"logs": logs[-100:], "total": len(logs)})


async def handle_index(request: web.Request) -> web.Response:
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return web.Response(text="index.html not found", status=404)
    return web.FileResponse(index_path)


async def _on_startup(app: web.Application) -> None:
    """Reset agent statuses on startup — subprocess는 이미 죽었으므로 idle로 복구."""
    conn = _get_db()
    try:
        updated = conn.execute(
            "UPDATE agents SET status = 'idle' WHERE status = 'running'"
        ).rowcount
        conn.commit()
        if updated:
            logger.info("서버 재시작: %d개 에이전트 상태를 idle로 복구", updated)
    finally:
        conn.close()


def create_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(_on_startup)
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/summary", handle_api_summary)
    # Daemon control
    app.router.add_post("/api/daemon/start", handle_api_daemon_start)
    app.router.add_post("/api/daemon/stop", handle_api_daemon_stop)
    app.router.add_get("/api/daemon/status", handle_api_daemon_status)
    app.router.add_get("/api/daemon/logs", handle_api_daemon_logs)
    # Campaigns
    app.router.add_get("/api/campaigns", handle_api_campaigns)
    app.router.add_post("/api/campaigns", handle_api_campaign_create)
    app.router.add_get("/api/campaigns/{campaign_id}/report", handle_api_campaign_report)
    app.router.add_patch("/api/campaigns/{campaign_id}", handle_api_campaign_update)
    # Agents (characters)
    app.router.add_get("/api/agents", handle_api_agents_list)
    app.router.add_post("/api/agents", handle_api_agent_create)
    app.router.add_patch("/api/agents/{agent_id}", handle_api_agent_update)
    app.router.add_delete("/api/agents/{agent_id}", handle_api_agent_delete)
    app.router.add_post("/api/agents/{agent_id}/start", handle_api_agent_start)
    app.router.add_post("/api/agents/{agent_id}/stop", handle_api_agent_stop)
    app.router.add_get("/api/agents/{agent_id}/logs", handle_api_agent_logs)
    # Data
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
