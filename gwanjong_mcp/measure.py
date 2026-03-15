"""Measurement — attribution, A/B experiments, weekly reports."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .storage import (
    DB_PATH,
    ensure_actions_tables,
    ensure_campaigns_table,
    ensure_conversions_table,
    ensure_experiments_table,
    ensure_replies_table,
    get_db,
)

logger = logging.getLogger(__name__)


class Measurement:
    """Campaign performance measurement + A/B experiments."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path

    def _get_db(self):
        conn = get_db(self._db_path)
        ensure_actions_tables(conn)
        ensure_campaigns_table(conn)
        ensure_conversions_table(conn)
        ensure_experiments_table(conn)
        ensure_replies_table(conn)
        return conn

    def campaign_attribution(self, campaign_id: str) -> dict[str, Any]:
        """Per-platform/action attribution for a campaign."""
        conn = self._get_db()
        try:
            by_platform = {}
            rows = conn.execute(
                "SELECT platform, action, COUNT(*) as cnt FROM actions WHERE campaign_id = ? GROUP BY platform, action",
                (campaign_id,),
            ).fetchall()
            for r in rows:
                by_platform.setdefault(r["platform"], {})[r["action"]] = r["cnt"]

            conv_rows = conn.execute(
                "SELECT source, medium, COUNT(*) as cnt FROM conversions WHERE campaign_id = ? GROUP BY source, medium",
                (campaign_id,),
            ).fetchall()
            conv_by_source = {}
            for r in conv_rows:
                conv_by_source.setdefault(r["source"], {})[r["medium"]] = r["cnt"]

            return {
                "campaign_id": campaign_id,
                "actions_by_platform": by_platform,
                "conversions_by_source": conv_by_source,
            }
        finally:
            conn.close()

    def action_performance(self, campaign_id: str) -> dict[str, Any]:
        """Reply rate and engagement metrics for a campaign."""
        conn = self._get_db()
        try:
            total_comments = conn.execute(
                "SELECT COUNT(*) FROM actions WHERE campaign_id = ? AND action = 'comment'",
                (campaign_id,),
            ).fetchone()[0]

            # 캠페인 소속 게시글에 대한 답글 수
            total_replies = conn.execute(
                """
                SELECT COUNT(*) FROM replies r
                JOIN actions a ON r.post_url = a.post_url
                WHERE a.campaign_id = ?
                """,
                (campaign_id,),
            ).fetchone()[0]

            reply_rate = round(total_replies / total_comments * 100, 1) if total_comments > 0 else 0

            total_posts = conn.execute(
                "SELECT COUNT(*) FROM actions WHERE campaign_id = ? AND action = 'post'",
                (campaign_id,),
            ).fetchone()[0]

            return {
                "campaign_id": campaign_id,
                "total_comments": total_comments,
                "total_posts": total_posts,
                "total_replies": total_replies,
                "reply_rate": reply_rate,
            }
        finally:
            conn.close()

    def ab_create(
        self,
        campaign_id: str,
        name: str,
        variant_a: str,
        variant_b: str,
        metric: str = "reply_rate",
    ) -> dict[str, Any]:
        """Create a simple A/B experiment."""
        exp_id = f"exp_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_db()
        try:
            conn.execute(
                "INSERT INTO experiments (id, campaign_id, name, variant_a, variant_b, metric, status, result_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (exp_id, campaign_id, name, variant_a, variant_b, metric, "running", "{}", now),
            )
            conn.commit()
        finally:
            conn.close()

        return {"id": exp_id, "name": name, "status": "running"}

    def ab_result(self, experiment_id: str) -> dict[str, Any]:
        """Get A/B experiment results."""
        conn = self._get_db()
        try:
            row = conn.execute(
                "SELECT * FROM experiments WHERE id = ?", (experiment_id,)
            ).fetchone()
            if not row:
                return {"error": f"실험 '{experiment_id}' 없음"}

            result = json.loads(row["result_json"])
            return {
                "id": row["id"],
                "campaign_id": row["campaign_id"],
                "name": row["name"],
                "variant_a": row["variant_a"],
                "variant_b": row["variant_b"],
                "metric": row["metric"],
                "status": row["status"],
                "result": result,
            }
        finally:
            conn.close()

    def ab_conclude(self, experiment_id: str, result: dict[str, Any]) -> dict[str, Any]:
        """Conclude an A/B experiment with results."""
        conn = self._get_db()
        try:
            conn.execute(
                "UPDATE experiments SET status = 'completed', result_json = ? WHERE id = ?",
                (json.dumps(result), experiment_id),
            )
            conn.commit()
        finally:
            conn.close()

        return {"id": experiment_id, "status": "completed", "result": result}

    def weekly_report(self, campaign_id: str) -> dict[str, Any]:
        """Generate a weekly performance report for a campaign."""
        conn = self._get_db()
        try:
            now = datetime.now(timezone.utc)
            week_ago = (now - timedelta(days=7)).isoformat()

            # 주간 활동
            weekly_actions = conn.execute(
                "SELECT COUNT(*) FROM actions WHERE campaign_id = ? AND timestamp >= ?",
                (campaign_id, week_ago),
            ).fetchone()[0]

            weekly_by_action = {
                r["action"]: r["cnt"]
                for r in conn.execute(
                    "SELECT action, COUNT(*) as cnt FROM actions WHERE campaign_id = ? AND timestamp >= ? GROUP BY action",
                    (campaign_id, week_ago),
                ).fetchall()
            }

            weekly_by_platform = {
                r["platform"]: r["cnt"]
                for r in conn.execute(
                    "SELECT platform, COUNT(*) as cnt FROM actions WHERE campaign_id = ? AND timestamp >= ? GROUP BY platform",
                    (campaign_id, week_ago),
                ).fetchall()
            }

            # 주간 전환
            weekly_conversions = conn.execute(
                "SELECT COUNT(*) FROM conversions WHERE campaign_id = ? AND created_at >= ?",
                (campaign_id, week_ago),
            ).fetchone()[0]

            # 일별 추이
            daily = []
            for i in range(6, -1, -1):
                day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM actions WHERE campaign_id = ? AND timestamp >= ? AND timestamp < date(?, '+1 day')",
                    (campaign_id, day, day),
                ).fetchone()[0]
                daily.append({"date": day, "count": cnt})

            # 실행 중인 실험
            experiments = [
                {"id": r["id"], "name": r["name"], "status": r["status"]}
                for r in conn.execute(
                    "SELECT id, name, status FROM experiments WHERE campaign_id = ? AND status = 'running'",
                    (campaign_id,),
                ).fetchall()
            ]

            return {
                "campaign_id": campaign_id,
                "period": {"from": week_ago[:10], "to": now.strftime("%Y-%m-%d")},
                "total_actions": weekly_actions,
                "by_action": weekly_by_action,
                "by_platform": weekly_by_platform,
                "total_conversions": weekly_conversions,
                "daily_trend": daily,
                "active_experiments": experiments,
            }
        finally:
            conn.close()

    def best_performing(self, campaign_id: str, metric: str = "actions") -> list[dict[str, Any]]:
        """Find best performing platforms/actions for a campaign."""
        conn = self._get_db()
        try:
            if metric == "actions":
                rows = conn.execute(
                    "SELECT platform, action, COUNT(*) as cnt FROM actions WHERE campaign_id = ? GROUP BY platform, action ORDER BY cnt DESC LIMIT 10",
                    (campaign_id,),
                ).fetchall()
                return [
                    {"platform": r["platform"], "action": r["action"], "count": r["cnt"]}
                    for r in rows
                ]
            elif metric == "conversions":
                rows = conn.execute(
                    "SELECT source, medium, COUNT(*) as cnt FROM conversions WHERE campaign_id = ? GROUP BY source, medium ORDER BY cnt DESC LIMIT 10",
                    (campaign_id,),
                ).fetchall()
                return [
                    {"source": r["source"], "medium": r["medium"], "count": r["cnt"]} for r in rows
                ]
            else:
                return []
        finally:
            conn.close()
