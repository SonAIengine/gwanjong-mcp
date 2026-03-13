"""Safety guard — Rate limiting + content validation. EventBus plugin."""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .events import Event, EventBus

logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("GWANJONG_DB_PATH", str(Path.home() / ".gwanjong" / "memory.db")))

# AI가 흔히 쓰는 패턴
AI_WORDS = {
    "fascinating",
    "insightful",
    "resonates",
    "game-changer",
    "deep dive",
    "kudos",
    "compelling",
    "groundbreaking",
}

AI_OPENERS = [
    r"^(this is (amazing|great|awesome|incredible))",
    r"^(great (article|post|write-up|read))",
    r"^(love this)",
    r"^(curious about)",
    r"^(i'd love to hear)",
]


@dataclass
class PlatformLimit:
    """Per-platform activity limits."""

    platform: str
    max_comments_per_day: int = 3
    max_posts_per_day: int = 1
    max_upvotes_per_day: int = 5
    min_interval_minutes: int = 30
    cooldown_after_error_minutes: int = 60


# 기본 제한 (GUIDE.md 기반)
DEFAULT_LIMITS: dict[str, PlatformLimit] = {
    "devto": PlatformLimit("devto", max_comments_per_day=3, max_posts_per_day=1),
    "bluesky": PlatformLimit("bluesky", max_comments_per_day=5, max_posts_per_day=2),
    "twitter": PlatformLimit("twitter", max_comments_per_day=5, max_posts_per_day=2),
    "reddit": PlatformLimit("reddit", max_comments_per_day=3, max_posts_per_day=0),
}


def _get_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Get SQLite connection. Creates table if not exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            action TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT DEFAULT 'ok'
        )
    """)
    conn.commit()
    return conn


class Safety:
    """Rate limiting + content validation. Attach to EventBus to automatically gate strike.before."""

    def __init__(
        self,
        limits: dict[str, PlatformLimit] | None = None,
        db_path: Path = DB_PATH,
    ) -> None:
        self.limits = limits or dict(DEFAULT_LIMITS)
        self._db_path = db_path

    def attach(self, bus: EventBus) -> None:
        """Connect to EventBus. Subscribes to strike.before/after events."""
        bus.on("strike.before", self._on_strike_before)
        bus.on("strike.after", self._on_strike_after)
        logger.info("Safety attached to EventBus")

    async def _on_strike_before(self, event: Event) -> bool | None:
        """Validate before strike. Returns False to block."""
        platform = event.data.get("platform", "")
        action = event.data.get("action", "")
        content = event.data.get("content", "")

        # 1. rate limit 체크
        ok, reason = self.check_rate_limit(platform, action)
        if not ok:
            logger.warning("Rate limit: %s", reason)
            return False

        # 2. 콘텐츠 검증 (upvote는 콘텐츠 없음)
        if action != "upvote" and content:
            ok, violations = self.validate_content(content, platform)
            if not ok:
                logger.warning("Content guard: %s", violations)
                return False

        return None  # 통과

    async def _on_strike_after(self, event: Event) -> None:
        """Record to rate_log after strike completion."""
        record = event.data.get("record")
        response = event.data.get("response", {})
        if record is None:
            return
        platform = record.platform if hasattr(record, "platform") else record.get("platform", "")
        action = record.action if hasattr(record, "action") else record.get("action", "")
        status = "ok" if response.get("status") == "posted" else "fail"
        self.record_action(platform, action, status)

    # ── Rate Limiter ──

    def check_rate_limit(self, platform: str, action: str) -> tuple[bool, str]:
        """Check if action is allowed. Returns (ok, reason if denied)."""
        limit = self.limits.get(platform)
        if limit is None:
            # 알 수 없는 플랫폼은 보수적 기본값
            limit = PlatformLimit(platform)

        conn = _get_db(self._db_path)
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # 일일 횟수 체크
            row = conn.execute(
                "SELECT COUNT(*) FROM rate_log WHERE platform=? AND action=? AND timestamp LIKE ? AND status='ok'",
                (platform, action, f"{today}%"),
            ).fetchone()
            count = row[0] if row else 0

            max_per_day = {
                "comment": limit.max_comments_per_day,
                "post": limit.max_posts_per_day,
                "upvote": limit.max_upvotes_per_day,
            }.get(action, 3)

            if count >= max_per_day:
                return False, f"{platform} {action} daily limit exceeded ({count}/{max_per_day})"

            # 최소 간격 체크
            row = conn.execute(
                "SELECT timestamp FROM rate_log WHERE platform=? AND status='ok' ORDER BY id DESC LIMIT 1",
                (platform,),
            ).fetchone()
            if row:
                last = datetime.fromisoformat(row[0])
                now = datetime.now(timezone.utc)
                elapsed = (now - last).total_seconds() / 60
                if elapsed < limit.min_interval_minutes:
                    remaining = limit.min_interval_minutes - elapsed
                    return False, f"{platform} cooldown active ({remaining:.0f}min remaining)"

            return True, ""
        finally:
            conn.close()

    def record_action(self, platform: str, action: str, status: str = "ok") -> None:
        """Record an action."""
        conn = _get_db(self._db_path)
        try:
            conn.execute(
                "INSERT INTO rate_log (platform, action, timestamp, status) VALUES (?, ?, ?, ?)",
                (platform, action, datetime.now(timezone.utc).isoformat(), status),
            )
            conn.commit()
        finally:
            conn.close()

    def get_daily_stats(self) -> dict[str, dict[str, int]]:
        """Today's per-platform activity statistics."""
        conn = _get_db(self._db_path)
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            rows = conn.execute(
                "SELECT platform, action, COUNT(*) FROM rate_log WHERE timestamp LIKE ? AND status='ok' GROUP BY platform, action",
                (f"{today}%",),
            ).fetchall()
            stats: dict[str, dict[str, int]] = {}
            for platform, action, count in rows:
                stats.setdefault(platform, {})[action] = count
            return stats
        finally:
            conn.close()

    # ── Content Guard ──

    def validate_content(self, content: str, platform: str = "") -> tuple[bool, list[str]]:
        """Validate content safety. Returns (passed, list of violations)."""
        violations: list[str] = []
        content_lower = content.lower()

        # 1. AI 단어 탐지
        found_ai = [w for w in AI_WORDS if w in content_lower]
        if found_ai:
            violations.append(f"AI pattern words: {', '.join(found_ai)}")

        # 2. AI 오프너 탐지
        for pattern in AI_OPENERS:
            if re.search(pattern, content_lower):
                violations.append(f"AI opener pattern: {pattern}")
                break

        # 3. 칭찬→경험→질문 공식 탐지
        has_praise = any(w in content_lower for w in ("great", "amazing", "love", "awesome"))
        has_experience = any(
            w in content_lower for w in ("in my experience", "i've found", "i tried")
        )
        has_question = content.rstrip().endswith("?")
        if has_praise and has_experience and has_question:
            violations.append("Formulaic structure: praise→experience→question")

        # 4. 길이 검증
        max_lengths = {"twitter": 280, "bluesky": 300}
        if platform in max_lengths and len(content) > max_lengths[platform]:
            violations.append(
                f"{platform} length exceeded ({len(content)}/{max_lengths[platform]})"
            )

        # 5. 자기 홍보 비율 (URL 개수)
        url_count = len(re.findall(r"https?://", content))
        if url_count > 1:
            violations.append(f"{url_count} URLs detected — possible self-promotion")

        return len(violations) == 0, violations
