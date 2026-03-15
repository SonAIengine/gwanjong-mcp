"""Safety guard — Rate limiting + content validation. EventBus plugin."""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .events import Event, EventBus
from .policy import DEFAULT_LIMITS, PlatformLimit
from .storage import DB_PATH, ensure_rate_log_table, get_db

logger = logging.getLogger(__name__)

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


def _get_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Get SQLite connection. Creates table if not exists."""
    conn = get_db(db_path)
    ensure_rate_log_table(conn)
    return conn


class Safety:
    """Rate limiting + content validation. Attach to EventBus to automatically gate strike.before."""

    # 연속 실패 임계값 — 이 횟수 이상 연속 실패하면 플랫폼 자동 차단
    CONSECUTIVE_FAIL_THRESHOLD = 3
    # 차단 지속 시간 (분) — 자동 차단 후 이 시간 동안 해당 플랫폼 차단
    PLATFORM_BAN_MINUTES = 60 * 24  # 24시간

    def __init__(
        self,
        limits: dict[str, PlatformLimit] | None = None,
        db_path: Path = DB_PATH,
    ) -> None:
        self.limits = limits or dict(DEFAULT_LIMITS)
        self._db_path = db_path
        # 플랫폼별 연속 실패 카운터
        self._consecutive_fails: dict[str, int] = {}
        # 플랫폼 자동 차단 시각 (UTC ISO string)
        self._platform_banned_until: dict[str, str] = {}

    def attach(self, bus: EventBus) -> None:
        """Connect to EventBus. Subscribes to strike.before/after/failed events."""
        bus.on("strike.before", self._on_strike_before)
        bus.on("strike.after", self._on_strike_after)
        bus.on("strike.failed", self._on_strike_failed)
        logger.info("Safety attached to EventBus")

    async def _on_strike_failed(self, event: Event) -> None:
        """Track consecutive failures for auto-ban."""
        platform = event.data.get("platform", "")
        error = event.data.get("error", "")
        if platform:
            self.record_strike_failure(platform, error)

    async def _on_strike_before(self, event: Event) -> str | None:
        """Validate before strike. Returns False to block."""
        platform = event.data.get("platform", "")
        action = event.data.get("action", "")
        content = event.data.get("content", "")

        # 0. 플랫폼 자동 차단 체크
        banned_until = self._platform_banned_until.get(platform, "")
        if banned_until:
            now = datetime.now(timezone.utc).isoformat()
            if now < banned_until:
                remaining = self._ban_remaining_hours(platform)
                reason = f"{platform} 자동 차단 중 (연속 실패 감지, {remaining}시간 후 해제)"
                logger.warning("Platform ban: %s", reason)
                return reason
            else:
                # 차단 해제
                del self._platform_banned_until[platform]
                self._consecutive_fails.pop(platform, None)
                logger.info("Platform ban 해제: %s", platform)

        # 1. rate limit 체크
        ok, reason = self.check_rate_limit(platform, action)
        if not ok:
            logger.warning("Rate limit: %s", reason)
            return reason

        # 2. 콘텐츠 검증 (upvote는 콘텐츠 없음)
        if action != "upvote" and content:
            ok, violations = self.validate_content(content, platform, action=action)
            if not ok:
                logger.warning("Content guard: %s", violations)
                return "; ".join(violations)

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

        # 성공하면 연속 실패 카운터 리셋
        if status == "ok":
            self._consecutive_fails.pop(platform, None)

    def record_strike_failure(self, platform: str, error: str = "") -> None:
        """Record a strike failure for consecutive fail tracking."""
        count = self._consecutive_fails.get(platform, 0) + 1
        self._consecutive_fails[platform] = count
        logger.warning(
            "Strike 실패 (%s): 연속 %d회 — %s",
            platform,
            count,
            error[:80],
        )

        if count >= self.CONSECUTIVE_FAIL_THRESHOLD:
            from datetime import timedelta

            ban_dt = datetime.now(timezone.utc) + timedelta(minutes=self.PLATFORM_BAN_MINUTES)
            self._platform_banned_until[platform] = ban_dt.isoformat()
            logger.warning(
                "🚫 %s 자동 차단: 연속 %d회 실패 → %d시간 차단",
                platform,
                count,
                self.PLATFORM_BAN_MINUTES // 60,
            )

    def _ban_remaining_hours(self, platform: str) -> int:
        banned_until = self._platform_banned_until.get(platform, "")
        if not banned_until:
            return 0
        try:
            ban_dt = datetime.fromisoformat(banned_until)
            remaining = (ban_dt - datetime.now(timezone.utc)).total_seconds() / 3600
            return max(0, int(remaining))
        except (ValueError, TypeError):
            return 0

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

    def validate_content(
        self, content: str, platform: str = "", *, action: str = "comment"
    ) -> tuple[bool, list[str]]:
        """Validate content safety. Returns (passed, list of violations)."""
        violations: list[str] = []
        content_lower = content.lower()
        is_post = action == "post"

        # 1. AI 단어 탐지 (post는 면제 — 글에서 기술 용어로 쓸 수 있음)
        if not is_post:
            found_ai = [w for w in AI_WORDS if w in content_lower]
            if found_ai:
                violations.append(f"AI pattern words: {', '.join(found_ai)}")

        # 2. AI 오프너 탐지 (post는 면제 — 글 도입부 자유)
        if not is_post:
            for pattern in AI_OPENERS:
                if re.search(pattern, content_lower):
                    violations.append(f"AI opener pattern: {pattern}")
                    break

        # 3. 칭찬→경험→질문 공식 탐지 (comment 전용)
        if not is_post:
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
        # post: URL 여러 개는 자연스러움 (10개까지 허용)
        # comment: URL 1개 초과 시 경고
        url_count = len(re.findall(r"https?://", content))
        url_limit = 10 if is_post else 1
        if url_count > url_limit:
            violations.append(f"{url_count} URLs detected — possible self-promotion")

        return len(violations) == 0, violations
