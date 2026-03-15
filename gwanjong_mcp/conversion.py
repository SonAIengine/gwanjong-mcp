"""Conversion tracking — UTM tagging + click/conversion recording. EventBus plugin."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .events import Event, EventBus
from .storage import DB_PATH, ensure_conversions_table, get_db
from .types import ConversionEvent

logger = logging.getLogger(__name__)


def generate_utm(campaign_id: str, platform: str, action: str) -> dict[str, str]:
    """Generate UTM parameters."""
    return {
        "utm_source": platform,
        "utm_medium": action,
        "utm_campaign": campaign_id,
    }


def inject_utm(content: str, utm_params: dict[str, str]) -> str:
    """Inject UTM parameters into URLs found in content."""
    url_pattern = re.compile(r'(https?://[^\s\)"\'>]+)')

    def _add_utm(match: re.Match) -> str:
        url = match.group(1)
        parsed = urlparse(url)
        existing = parse_qs(parsed.query)
        # UTM이 이미 있으면 건너뛰기
        if any(k.startswith("utm_") for k in existing):
            return url
        separator = "&" if parsed.query else ""
        new_query = parsed.query + separator + urlencode(utm_params)
        return urlunparse(parsed._replace(query=new_query))

    return url_pattern.sub(_add_utm, content)


class ConversionTracker:
    """UTM injection + conversion event recording. EventBus plugin."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path

    def attach(self, bus: EventBus) -> None:
        """Attach to EventBus."""
        bus.on("strike.before", self._on_strike_before)
        bus.on("strike.after", self._on_strike_after)
        logger.info("ConversionTracker attached to EventBus")

    async def _on_strike_before(self, event: Event) -> None:
        """Inject UTM parameters into content URLs if campaign_id present."""
        campaign_id = event.data.get("campaign_id", "")
        if not campaign_id:
            return
        content = event.data.get("content", "")
        platform = event.data.get("platform", "")
        action = event.data.get("action", "")

        if content and platform:
            utm = generate_utm(campaign_id, platform, action)
            event.data["content"] = inject_utm(content, utm)
            event.data["utm_params"] = utm

    async def _on_strike_after(self, event: Event) -> None:
        """Record conversion event if campaign_id present."""
        campaign_id = event.data.get("campaign_id", "")
        if not campaign_id:
            return

        record = event.data.get("record")
        response = event.data.get("response", {})
        if record is None or response.get("status") != "posted":
            return

        platform = record.platform if hasattr(record, "platform") else record.get("platform", "")
        action = record.action if hasattr(record, "action") else record.get("action", "")
        url = record.url if hasattr(record, "url") else record.get("url", "")

        conv = ConversionEvent(
            id=f"conv_{uuid.uuid4().hex[:8]}",
            campaign_id=campaign_id,
            source=platform,
            medium=action,
            url=url,
            event_type="click",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        conn = get_db(self._db_path)
        ensure_conversions_table(conn)
        try:
            conn.execute(
                "INSERT INTO conversions (id, campaign_id, source, medium, action_id, url, event_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conv.id,
                    conv.campaign_id,
                    conv.source,
                    conv.medium,
                    conv.action_id,
                    conv.url,
                    conv.event_type,
                    conv.created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("Conversion recorded: %s %s/%s", conv.campaign_id, conv.source, conv.medium)

    def get_stats(self, campaign_id: str) -> dict[str, Any]:
        """Get conversion stats for a campaign."""
        conn = get_db(self._db_path)
        ensure_conversions_table(conn)
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM conversions WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()[0]

            by_source = {
                r["source"]: r["cnt"]
                for r in conn.execute(
                    "SELECT source, COUNT(*) as cnt FROM conversions WHERE campaign_id = ? GROUP BY source",
                    (campaign_id,),
                ).fetchall()
            }

            by_type = {
                r["event_type"]: r["cnt"]
                for r in conn.execute(
                    "SELECT event_type, COUNT(*) as cnt FROM conversions WHERE campaign_id = ? GROUP BY event_type",
                    (campaign_id,),
                ).fetchall()
            }

            return {
                "campaign_id": campaign_id,
                "total": total,
                "by_source": by_source,
                "by_type": by_type,
            }
        finally:
            conn.close()

    def record_event(
        self,
        campaign_id: str,
        source: str,
        medium: str,
        event_type: str = "click",
        url: str = "",
    ) -> ConversionEvent:
        """Manually record a conversion event."""
        conv = ConversionEvent(
            id=f"conv_{uuid.uuid4().hex[:8]}",
            campaign_id=campaign_id,
            source=source,
            medium=medium,
            url=url,
            event_type=event_type,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        conn = get_db(self._db_path)
        ensure_conversions_table(conn)
        try:
            conn.execute(
                "INSERT INTO conversions (id, campaign_id, source, medium, action_id, url, event_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conv.id,
                    conv.campaign_id,
                    conv.source,
                    conv.medium,
                    conv.action_id,
                    conv.url,
                    conv.event_type,
                    conv.created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return conv
