"""Message framework — ICP-based messaging management."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import DB_PATH, ensure_message_frames_table, get_db
from .types import MessageFrame

logger = logging.getLogger(__name__)


class MessageFramework:
    """MessageFrame CRUD + context-based hook selection."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path

    def _get_db(self):
        conn = get_db(self._db_path)
        ensure_message_frames_table(conn)
        return conn

    def create(self, data: dict[str, Any]) -> MessageFrame:
        """Create a new message frame."""
        now = datetime.now(timezone.utc).isoformat()
        frame = MessageFrame(
            id=data.get("id", f"mf_{uuid.uuid4().hex[:8]}"),
            campaign_id=data["campaign_id"],
            persona_segment=data["persona_segment"],
            value_prop=data.get("value_prop", ""),
            proof_points=data.get("proof_points", []),
            objections=data.get("objections", {}),
            hooks=data.get("hooks", []),
            created_at=now,
        )

        conn = self._get_db()
        try:
            conn.execute(
                """
                INSERT INTO message_frames (id, campaign_id, persona_segment, value_prop,
                    proof_points_json, objections_json, hooks_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    frame.id,
                    frame.campaign_id,
                    frame.persona_segment,
                    frame.value_prop,
                    json.dumps(frame.proof_points),
                    json.dumps(frame.objections),
                    json.dumps(frame.hooks),
                    frame.created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("MessageFrame created: %s (%s)", frame.id, frame.persona_segment)
        return frame

    def get(self, frame_id: str) -> MessageFrame | None:
        """Get a message frame by ID."""
        conn = self._get_db()
        try:
            row = conn.execute("SELECT * FROM message_frames WHERE id = ?", (frame_id,)).fetchone()
            if not row:
                return None
            return self._row_to_frame(row)
        finally:
            conn.close()

    def list_by_campaign(self, campaign_id: str) -> list[MessageFrame]:
        """List all message frames for a campaign."""
        conn = self._get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM message_frames WHERE campaign_id = ? ORDER BY created_at DESC",
                (campaign_id,),
            ).fetchall()
            return [self._row_to_frame(r) for r in rows]
        finally:
            conn.close()

    def select_hook(self, campaign_id: str, platform: str = "", tone: str = "") -> str | None:
        """Select a contextually appropriate hook from campaign frames."""
        frames = self.list_by_campaign(campaign_id)
        if not frames:
            return None

        # 간단한 매칭: 첫 번째 프레임의 첫 hook 반환 (향후 tone/platform 매칭 강화 가능)
        for frame in frames:
            if frame.hooks:
                return frame.hooks[0]

        return None

    def get_objection_response(self, campaign_id: str, objection: str) -> str | None:
        """Find a response to a specific objection."""
        frames = self.list_by_campaign(campaign_id)
        objection_lower = objection.lower()

        for frame in frames:
            for key, response in frame.objections.items():
                if key.lower() in objection_lower or objection_lower in key.lower():
                    return response

        return None

    @staticmethod
    def _row_to_frame(row) -> MessageFrame:
        return MessageFrame(
            id=row["id"],
            campaign_id=row["campaign_id"],
            persona_segment=row["persona_segment"],
            value_prop=row["value_prop"],
            proof_points=json.loads(row["proof_points_json"]),
            objections=json.loads(row["objections_json"]),
            hooks=json.loads(row["hooks_json"]),
            created_at=row["created_at"],
        )
