"""Asset library — save, search, and reuse content assets."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .storage import DB_PATH, ensure_assets_table, get_db
from .types import Asset

logger = logging.getLogger(__name__)


class AssetLibrary:
    """Content asset CRUD + usage tracking."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path

    def _get_db(self):
        conn = get_db(self._db_path)
        ensure_assets_table(conn)
        return conn

    def save(self, data: dict[str, Any]) -> Asset:
        """Save a new asset."""
        now = datetime.now(timezone.utc).isoformat()
        asset = Asset(
            id=data.get("id", f"asset_{uuid.uuid4().hex[:8]}"),
            campaign_id=data.get("campaign_id"),
            asset_type=data.get("asset_type", "snippet"),
            platform=data.get("platform", ""),
            content=data["content"],
            tags=data.get("tags", []),
            usage_count=0,
            last_used="",
            created_at=now,
        )

        conn = self._get_db()
        try:
            conn.execute(
                """
                INSERT INTO assets (id, campaign_id, asset_type, platform, content, tags_json, usage_count, last_used, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset.id,
                    asset.campaign_id,
                    asset.asset_type,
                    asset.platform,
                    asset.content,
                    json.dumps(asset.tags),
                    asset.usage_count,
                    asset.last_used,
                    asset.created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Asset saved: %s (%s)", asset.id, asset.asset_type)
        return asset

    def get(self, asset_id: str) -> Asset | None:
        """Get an asset by ID."""
        conn = self._get_db()
        try:
            row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if not row:
                return None
            return self._row_to_asset(row)
        finally:
            conn.close()

    def search(
        self,
        query: str = "",
        asset_type: str = "",
        platform: str = "",
        campaign_id: str = "",
        limit: int = 20,
    ) -> list[Asset]:
        """Search assets by keyword, type, platform, or campaign."""
        conn = self._get_db()
        try:
            conditions = []
            params: list[Any] = []

            if query:
                conditions.append("(content LIKE ? OR tags_json LIKE ?)")
                params.extend([f"%{query}%", f"%{query}%"])
            if asset_type:
                conditions.append("asset_type = ?")
                params.append(asset_type)
            if platform:
                conditions.append("(platform = ? OR platform = '')")
                params.append(platform)
            if campaign_id:
                conditions.append("(campaign_id = ? OR campaign_id IS NULL)")
                params.append(campaign_id)

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            params.append(limit)

            rows = conn.execute(
                f"SELECT * FROM assets {where} ORDER BY usage_count DESC, created_at DESC LIMIT ?",
                params,
            ).fetchall()
            return [self._row_to_asset(r) for r in rows]
        finally:
            conn.close()

    def use(self, asset_id: str) -> Asset | None:
        """Mark an asset as used (increment usage_count)."""
        conn = self._get_db()
        try:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE assets SET usage_count = usage_count + 1, last_used = ? WHERE id = ?",
                (now, asset_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if not row:
                return None
            return self._row_to_asset(row)
        finally:
            conn.close()

    def list_top(self, limit: int = 10) -> list[Asset]:
        """List most-used assets."""
        conn = self._get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM assets ORDER BY usage_count DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_asset(r) for r in rows]
        finally:
            conn.close()

    def list_recent(self, limit: int = 10) -> list[Asset]:
        """List recently created assets."""
        conn = self._get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM assets ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_asset(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _row_to_asset(row) -> Asset:
        return Asset(
            id=row["id"],
            campaign_id=row["campaign_id"],
            asset_type=row["asset_type"],
            platform=row["platform"],
            content=row["content"],
            tags=json.loads(row["tags_json"]),
            usage_count=row["usage_count"],
            last_used=row["last_used"],
            created_at=row["created_at"],
        )
