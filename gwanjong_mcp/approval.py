"""Approval queue — a waiting queue where autonomous mode saves items before strike."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import pipeline
from .events import Event, EventBus
from .memory import Memory
from .safety import Safety
from .tracker import Tracker
from .types import DraftContext, Opportunity

DB_PATH = Path(os.getenv("GWANJONG_DB_PATH", str(Path.home() / ".gwanjong" / "memory.db")))


def _get_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
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
    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(approval_queue)").fetchall()
    }
    if "executed_at" not in columns:
        conn.execute("ALTER TABLE approval_queue ADD COLUMN executed_at TEXT")
    if "last_error" not in columns:
        conn.execute("ALTER TABLE approval_queue ADD COLUMN last_error TEXT")
    conn.commit()
    return conn


@dataclass
class ApprovalItem:
    """An item pending approval."""

    id: int
    topic: str
    platform: str
    action: str
    status: str
    opportunity_id: str
    post_id: str
    post_url: str
    title: str
    content: str
    context_json: str
    opportunity_json: str
    created_at: str
    reviewed_at: str | None
    executed_at: str | None
    last_error: str | None


class ApprovalQueue:
    """SQLite-backed approval queue."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path

    def enqueue(
        self,
        topic: str,
        opportunity: Opportunity,
        context: DraftContext,
        action: str,
        content: str,
    ) -> ApprovalItem:
        conn = _get_db(self._db_path)
        try:
            created_at = datetime.now(timezone.utc).isoformat()
            cursor = conn.execute(
                """
                INSERT INTO approval_queue (
                    topic, platform, action, status, opportunity_id, post_id,
                    post_url, title, content, context_json, opportunity_json, created_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic,
                    opportunity.platform,
                    action,
                    opportunity.id,
                    opportunity.post_id,
                    opportunity.url,
                    opportunity.title,
                    content,
                    json.dumps(asdict(context), ensure_ascii=True),
                    json.dumps(asdict(opportunity), ensure_ascii=True),
                    created_at,
                ),
            )
            conn.commit()
            item_id = int(cursor.lastrowid)
            row = conn.execute(
                "SELECT * FROM approval_queue WHERE id = ?",
                (item_id,),
            ).fetchone()
            assert row is not None
            return ApprovalItem(**dict(row))
        finally:
            conn.close()

    def get_pending(self, platform: str | None = None) -> list[dict[str, Any]]:
        conn = _get_db(self._db_path)
        try:
            if platform:
                rows = conn.execute(
                    "SELECT * FROM approval_queue WHERE status = 'pending' AND platform = ? ORDER BY id DESC",
                    (platform,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM approval_queue WHERE status = 'pending' ORDER BY id DESC",
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_failed(self, platform: str | None = None) -> list[dict[str, Any]]:
        conn = _get_db(self._db_path)
        try:
            if platform:
                rows = conn.execute(
                    "SELECT * FROM approval_queue WHERE status = 'failed' AND platform = ? ORDER BY id DESC",
                    (platform,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM approval_queue WHERE status = 'failed' ORDER BY id DESC",
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        conn = _get_db(self._db_path)
        try:
            row = conn.execute(
                "SELECT * FROM approval_queue WHERE id = ?",
                (item_id,),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def mark_approved(self, item_id: int) -> None:
        self._update_status(item_id, "approved", allowed_current_statuses={"pending"})

    def mark_rejected(self, item_id: int) -> None:
        self._update_status(item_id, "rejected", allowed_current_statuses={"pending"})

    def stats(self) -> dict[str, int]:
        conn = _get_db(self._db_path)
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM approval_queue GROUP BY status",
            ).fetchall()
            counts = {row["status"]: row["cnt"] for row in rows}
            return {
                "pending": counts.get("pending", 0),
                "approved": counts.get("approved", 0),
                "rejected": counts.get("rejected", 0),
                "executing": counts.get("executing", 0),
                "posted": counts.get("posted", 0),
                "failed": counts.get("failed", 0),
            }
        finally:
            conn.close()

    async def execute_approved(
        self,
        item_id: int,
        bus: EventBus | None = None,
    ) -> dict[str, Any]:
        item = self._claim_for_execution(item_id, {"pending", "approved"})
        return await self._execute_claimed_item(item, bus)

    async def retry_failed(
        self,
        item_id: int,
        bus: EventBus | None = None,
    ) -> dict[str, Any]:
        item = self._claim_for_execution(item_id, {"failed"})
        return await self._execute_claimed_item(item, bus)

    async def _execute_claimed_item(
        self,
        item: dict[str, Any],
        bus: EventBus | None = None,
    ) -> dict[str, Any]:
        queue_bus = bus or self._build_bus()
        context = DraftContext(**json.loads(item["context_json"]))
        context.opportunity_id = item["post_id"]

        try:
            record, response = await pipeline.strike(
                context,
                item["action"],
                item["content"],
                bus=queue_bus,
            )
        except Exception as exc:
            self._update_status(
                item["id"],
                "failed",
                executed_at=datetime.now(timezone.utc).isoformat(),
                last_error=str(exc),
            )
            await queue_bus.emit(Event("approval.executed", {
                "item_id": item["id"],
                "status": "failed",
                "error": str(exc),
            }))
            raise

        status = "posted" if response.get("status") == "posted" else "failed"
        self._update_status(
            item["id"],
            status,
            executed_at=datetime.now(timezone.utc).isoformat(),
            last_error=response.get("error"),
        )
        await queue_bus.emit(Event("approval.executed", {
            "item_id": item["id"],
            "status": status,
            "response": response,
            "record": asdict(record) if is_dataclass(record) else dict(vars(record)),
        }))
        return {
            "id": item["id"],
            "queue_status": status,
            "response": response,
        }

    def _build_bus(self) -> EventBus:
        bus = EventBus()
        Safety().attach(bus)
        Memory().attach(bus)
        Tracker().attach(bus)
        return bus

    def _update_status(
        self,
        item_id: int,
        status: str,
        *,
        executed_at: str | None = None,
        last_error: str | None = None,
        allowed_current_statuses: set[str] | None = None,
    ) -> None:
        conn = _get_db(self._db_path)
        try:
            query = """
                UPDATE approval_queue
                SET status = ?, reviewed_at = ?, executed_at = COALESCE(?, executed_at), last_error = ?
                WHERE id = ?
            """
            params: list[Any] = [
                status,
                datetime.now(timezone.utc).isoformat(),
                executed_at,
                last_error,
                item_id,
            ]
            if allowed_current_statuses:
                placeholders = ",".join("?" for _ in allowed_current_statuses)
                query += f" AND status IN ({placeholders})"
                params.extend(sorted(allowed_current_statuses))
            cursor = conn.execute(query, params)
            conn.commit()
            if cursor.rowcount == 0:
                item = self.get_item(item_id)
                if item is None:
                    raise ValueError(f"approval item not found: {item_id}")
                raise ValueError(
                    f"approval item cannot transition from '{item['status']}' to '{status}'"
                )
        finally:
            conn.close()

    def _claim_for_execution(self, item_id: int, allowed_statuses: set[str]) -> dict[str, Any]:
        conn = _get_db(self._db_path)
        try:
            reviewed_at = datetime.now(timezone.utc).isoformat()
            allowed_list = sorted(allowed_statuses)
            placeholders = ",".join("?" for _ in allowed_list)
            cursor = conn.execute(
                f"""
                UPDATE approval_queue
                SET status = 'executing', reviewed_at = ?, last_error = NULL
                WHERE id = ? AND status IN ({placeholders})
                """,
                [reviewed_at, item_id, *allowed_list],
            )
            conn.commit()
            if cursor.rowcount == 0:
                row = conn.execute(
                    "SELECT * FROM approval_queue WHERE id = ?",
                    (item_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"approval item not found: {item_id}")
                raise ValueError(f"approval item is not executable: {row['status']}")
            row = conn.execute(
                "SELECT * FROM approval_queue WHERE id = ?",
                (item_id,),
            ).fetchone()
            assert row is not None
            return dict(row)
        finally:
            conn.close()
