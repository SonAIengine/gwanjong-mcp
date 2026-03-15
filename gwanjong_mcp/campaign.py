"""Campaign management — CRUD + KPI reporting. EventBus plugin."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import EventBus
from .storage import (
    DB_PATH,
    ensure_actions_tables,
    ensure_campaigns_table,
    ensure_conversions_table,
    get_db,
)
from .types import Campaign

logger = logging.getLogger(__name__)


class CampaignManager:
    """Campaign CRUD + KPI aggregation."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path

    def _get_db(self):
        conn = get_db(self._db_path)
        ensure_campaigns_table(conn)
        ensure_actions_tables(conn)
        return conn

    def create(self, data: dict[str, Any], bus: EventBus | None = None) -> Campaign:
        """Create a new campaign."""
        now = datetime.now(timezone.utc).isoformat()
        camp = Campaign(
            id=data.get("id", f"camp_{uuid.uuid4().hex[:8]}"),
            name=data["name"],
            objective=data.get("objective", "awareness"),
            topics=data.get("topics", []),
            platforms=data.get("platforms", []),
            icp=data.get("icp", ""),
            cta=data.get("cta", ""),
            kpi_target=data.get("kpi_target", {}),
            start_date=data.get("start_date", now[:10]),
            end_date=data.get("end_date"),
            status=data.get("status", "active"),
            created_at=now,
        )

        conn = self._get_db()
        try:
            conn.execute(
                """
                INSERT INTO campaigns (id, name, objective, topics_json, platforms_json,
                    icp, cta, kpi_target_json, start_date, end_date, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    camp.id,
                    camp.name,
                    camp.objective,
                    json.dumps(camp.topics),
                    json.dumps(camp.platforms),
                    camp.icp,
                    camp.cta,
                    json.dumps(camp.kpi_target),
                    camp.start_date,
                    camp.end_date,
                    camp.status,
                    camp.created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Campaign created: %s (%s)", camp.id, camp.name)
        return camp

    def get(self, campaign_id: str) -> Campaign | None:
        """Get a campaign by ID."""
        conn = self._get_db()
        try:
            row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
            if not row:
                return None
            return self._row_to_campaign(row)
        finally:
            conn.close()

    def list_active(self) -> list[Campaign]:
        """List all active campaigns."""
        conn = self._get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM campaigns WHERE status IN ('active', 'draft') ORDER BY created_at DESC"
            ).fetchall()
            return [self._row_to_campaign(r) for r in rows]
        finally:
            conn.close()

    def list_all(self) -> list[Campaign]:
        """List all campaigns."""
        conn = self._get_db()
        try:
            rows = conn.execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()
            return [self._row_to_campaign(r) for r in rows]
        finally:
            conn.close()

    def update(self, campaign_id: str, data: dict[str, Any]) -> Campaign | None:
        """Update campaign fields."""
        camp = self.get(campaign_id)
        if not camp:
            return None

        conn = self._get_db()
        try:
            updates = []
            params: list[Any] = []
            field_map = {
                "name": "name",
                "objective": "objective",
                "icp": "icp",
                "cta": "cta",
                "start_date": "start_date",
                "end_date": "end_date",
                "status": "status",
            }
            json_fields = {
                "topics": "topics_json",
                "platforms": "platforms_json",
                "kpi_target": "kpi_target_json",
            }

            for key, col in field_map.items():
                if key in data:
                    updates.append(f"{col} = ?")
                    params.append(data[key])

            for key, col in json_fields.items():
                if key in data:
                    updates.append(f"{col} = ?")
                    params.append(json.dumps(data[key]))

            if not updates:
                return camp

            params.append(campaign_id)
            conn.execute(
                f"UPDATE campaigns SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
        finally:
            conn.close()

        return self.get(campaign_id)

    def get_report(self, campaign_id: str) -> dict[str, Any]:
        """Generate campaign performance report."""
        camp = self.get(campaign_id)
        if not camp:
            return {"error": f"캠페인 '{campaign_id}' 없음"}

        conn = self._get_db()
        try:
            # actions 집계
            total_actions = conn.execute(
                "SELECT COUNT(*) FROM actions WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()[0]

            by_platform = {
                r["platform"]: r["cnt"]
                for r in conn.execute(
                    "SELECT platform, COUNT(*) as cnt FROM actions WHERE campaign_id = ? GROUP BY platform",
                    (campaign_id,),
                ).fetchall()
            }

            by_action = {
                r["action"]: r["cnt"]
                for r in conn.execute(
                    "SELECT action, COUNT(*) as cnt FROM actions WHERE campaign_id = ? GROUP BY action",
                    (campaign_id,),
                ).fetchall()
            }

            # conversion 집계
            try:
                ensure_conversions_table(conn)
                total_conversions = conn.execute(
                    "SELECT COUNT(*) FROM conversions WHERE campaign_id = ?", (campaign_id,)
                ).fetchone()[0]
                by_event_type = {
                    r["event_type"]: r["cnt"]
                    for r in conn.execute(
                        "SELECT event_type, COUNT(*) as cnt FROM conversions WHERE campaign_id = ? GROUP BY event_type",
                        (campaign_id,),
                    ).fetchall()
                }
            except Exception:
                total_conversions = 0
                by_event_type = {}

            # KPI 대비 달성률
            kpi_progress = {}
            if camp.kpi_target:
                for metric, target in camp.kpi_target.items():
                    if metric == "comments":
                        actual = by_action.get("comment", 0)
                    elif metric == "posts":
                        actual = by_action.get("post", 0)
                    elif metric == "conversions":
                        actual = total_conversions
                    else:
                        actual = 0
                    kpi_progress[metric] = {
                        "target": target,
                        "actual": actual,
                        "progress": round(actual / target * 100, 1) if target > 0 else 0,
                    }

            return {
                "campaign": {
                    "id": camp.id,
                    "name": camp.name,
                    "objective": camp.objective,
                    "status": camp.status,
                    "topics": camp.topics,
                    "platforms": camp.platforms,
                },
                "total_actions": total_actions,
                "by_platform": by_platform,
                "by_action": by_action,
                "total_conversions": total_conversions,
                "conversions_by_type": by_event_type,
                "kpi_progress": kpi_progress,
            }
        finally:
            conn.close()

    @staticmethod
    def _row_to_campaign(row) -> Campaign:
        return Campaign(
            id=row["id"],
            name=row["name"],
            objective=row["objective"],
            topics=json.loads(row["topics_json"]),
            platforms=json.loads(row["platforms_json"]),
            icp=row["icp"],
            cta=row["cta"],
            kpi_target=json.loads(row["kpi_target_json"]),
            start_date=row["start_date"],
            end_date=row["end_date"],
            status=row["status"],
            created_at=row["created_at"],
        )
