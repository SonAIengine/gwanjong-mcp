"""Content scheduler — timed publication via EventBus. EventBus plugin."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import Event, EventBus
from .storage import DB_PATH, ensure_schedule_table, get_db
from .types import ScheduleItem

logger = logging.getLogger(__name__)


class Scheduler:
    """Schedule content for future publication. EventBus plugin."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path

    def _get_db(self):
        conn = get_db(self._db_path)
        ensure_schedule_table(conn)
        return conn

    def attach(self, bus: EventBus) -> None:
        """Attach to EventBus (listens for schedule.due internally)."""
        logger.info("Scheduler attached to EventBus")

    def add(self, data: dict[str, Any]) -> ScheduleItem:
        """Add a scheduled item."""
        now = datetime.now(timezone.utc).isoformat()
        item = ScheduleItem(
            id=data.get("id", f"sched_{uuid.uuid4().hex[:8]}"),
            campaign_id=data["campaign_id"],
            platform=data["platform"],
            action=data.get("action", "post"),
            content=data["content"],
            scheduled_at=data["scheduled_at"],
            status="pending",
            asset_ids=data.get("asset_ids", []),
            created_at=now,
        )

        conn = self._get_db()
        try:
            conn.execute(
                """
                INSERT INTO schedule (id, campaign_id, platform, action, content, scheduled_at,
                    status, asset_ids_json, published_at, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.campaign_id,
                    item.platform,
                    item.action,
                    item.content,
                    item.scheduled_at,
                    item.status,
                    json.dumps(item.asset_ids),
                    item.published_at,
                    item.error,
                    item.created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Scheduled: %s at %s on %s", item.id, item.scheduled_at, item.platform)
        return item

    def list_pending(self, campaign_id: str = "") -> list[ScheduleItem]:
        """List pending scheduled items."""
        conn = self._get_db()
        try:
            if campaign_id:
                rows = conn.execute(
                    "SELECT * FROM schedule WHERE status = 'pending' AND campaign_id = ? ORDER BY scheduled_at",
                    (campaign_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM schedule WHERE status = 'pending' ORDER BY scheduled_at"
                ).fetchall()
            return [self._row_to_item(r) for r in rows]
        finally:
            conn.close()

    def list_all(self, campaign_id: str = "", limit: int = 50) -> list[ScheduleItem]:
        """List all scheduled items."""
        conn = self._get_db()
        try:
            if campaign_id:
                rows = conn.execute(
                    "SELECT * FROM schedule WHERE campaign_id = ? ORDER BY scheduled_at DESC LIMIT ?",
                    (campaign_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM schedule ORDER BY scheduled_at DESC LIMIT ?", (limit,)
                ).fetchall()
            return [self._row_to_item(r) for r in rows]
        finally:
            conn.close()

    def cancel(self, item_id: str) -> bool:
        """Cancel a pending scheduled item."""
        conn = self._get_db()
        try:
            result = conn.execute(
                "UPDATE schedule SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
                (item_id,),
            )
            conn.commit()
            return result.rowcount > 0
        finally:
            conn.close()

    def check_due(self) -> list[ScheduleItem]:
        """Find items that are due for execution."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM schedule WHERE status = 'pending' AND scheduled_at <= ? ORDER BY scheduled_at",
                (now,),
            ).fetchall()
            return [self._row_to_item(r) for r in rows]
        finally:
            conn.close()

    async def execute(self, item: ScheduleItem, bus: EventBus | None = None) -> dict[str, Any]:
        """Execute a due schedule item via pipeline.strike."""
        from . import pipeline
        from .types import DraftContext

        ctx = DraftContext(
            opportunity_id=f"sched_{item.id}",
            platform=item.platform,
            title="",
            body_summary="",
            post_id="",
            suggested_approach=item.action,
        )

        conn = self._get_db()
        try:
            try:
                record, response = await pipeline.strike(ctx, item.action, item.content, bus=bus)
                now = datetime.now(timezone.utc).isoformat()

                if response.get("status") == "posted":
                    conn.execute(
                        "UPDATE schedule SET status = 'published', published_at = ? WHERE id = ?",
                        (now, item.id),
                    )
                    if bus:
                        await bus.emit(
                            Event(
                                "schedule.published",
                                {
                                    "item_id": item.id,
                                    "campaign_id": item.campaign_id,
                                    "platform": item.platform,
                                    "action": item.action,
                                },
                            )
                        )
                else:
                    error = response.get("error", "unknown error")
                    conn.execute(
                        "UPDATE schedule SET status = 'failed', error = ? WHERE id = ?",
                        (error, item.id),
                    )

                conn.commit()
                return {"item_id": item.id, "status": response.get("status", "failed"), **response}

            except Exception as e:
                conn.execute(
                    "UPDATE schedule SET status = 'failed', error = ? WHERE id = ?",
                    (str(e), item.id),
                )
                conn.commit()
                return {"item_id": item.id, "status": "failed", "error": str(e)}
        finally:
            conn.close()

    async def process_due(self, bus: EventBus | None = None) -> list[dict[str, Any]]:
        """Check and execute all due items."""
        due_items = self.check_due()
        results = []
        for item in due_items:
            result = await self.execute(item, bus=bus)
            results.append(result)
            logger.info("Schedule executed: %s → %s", item.id, result.get("status"))
        return results

    @staticmethod
    def _row_to_item(row) -> ScheduleItem:
        return ScheduleItem(
            id=row["id"],
            campaign_id=row["campaign_id"],
            platform=row["platform"],
            action=row["action"],
            content=row["content"],
            scheduled_at=row["scheduled_at"],
            status=row["status"],
            asset_ids=json.loads(row["asset_ids_json"]),
            published_at=row["published_at"],
            error=row["error"],
            created_at=row["created_at"],
        )
