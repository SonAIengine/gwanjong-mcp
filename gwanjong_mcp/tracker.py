"""Reply tracking — detect new replies on posts where comments were left. EventBus plugin."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from devhub.registry import get_adapter_class

from .events import Event, EventBus
from .memory import _get_db
from .storage import DB_PATH, ensure_replies_table

logger = logging.getLogger(__name__)


@dataclass
class DetectedReply:
    """A detected reply."""

    comment_id: str
    platform: str
    post_url: str
    parent_comment_id: str
    author: str
    body: str
    post_title: str = ""


class Tracker:
    """Reply tracker. Scans posts where comments were left to detect new replies.

    EventBus integration:
    - Subscribes to strike.after: automatically registers tracking targets when comments are posted
    - Emits reply.detected: allows other modules (e.g. autonomous) to respond
    """

    # 플랫폼별 gwanjong 사용자명 (환경변수에서 가져옴)
    _usernames: dict[str, str] = {}

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path

    def attach(self, bus: EventBus) -> None:
        """Attach to the EventBus."""
        bus.on("strike.after", self._on_strike_after)
        logger.info("Tracker attached to EventBus")

    async def _on_strike_after(self, event: Event) -> None:
        """Record tracking target if the completed strike was a comment (no extra storage needed since memory.actions already persists it)."""
        # Tracker는 scan 시 memory.actions를 읽으므로 여기서는 로깅만
        record = event.data.get("record")
        if record and getattr(record, "action", None) == "comment":
            logger.debug(
                "Tracker: comment posted, will track replies on next scan: %s",
                getattr(record, "url", ""),
            )

    async def scan(
        self,
        bus: EventBus | None = None,
        platforms: list[str] | None = None,
        limit: int = 20,
    ) -> list[DetectedReply]:
        """Read comment history from memory.db and scan those posts for replies.

        Returns:
            List of newly detected replies
        """
        conn = _get_db(self._db_path)
        ensure_replies_table(conn)
        try:
            # 1. 최근 댓글 이력 조회
            query = (
                "SELECT DISTINCT platform, post_url, opportunity_id, "
                "COALESCE(NULLIF(post_id, ''), opportunity_id) AS target_post_id "
                "FROM actions WHERE action = 'comment' AND post_url != ''"
            )
            params: list[Any] = []
            if platforms:
                placeholders = ",".join("?" * len(platforms))
                query += f" AND platform IN ({placeholders})"
                params.extend(platforms)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        if not rows:
            logger.info("Tracker: no comment history to track")
            return []

        # 2. 플랫폼별로 게시글 그룹화
        post_groups: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            platform = row["platform"]
            post_groups.setdefault(platform, []).append(
                {
                    "post_url": row["post_url"],
                    "opportunity_id": row["opportunity_id"] or "",
                    "post_id": row["target_post_id"] or "",
                }
            )

        # 3. 각 게시글의 댓글 트리를 가져와서 답글 감지
        all_replies: list[DetectedReply] = []

        for platform, posts in post_groups.items():
            try:
                replies = await self._scan_platform(platform, posts)
                all_replies.extend(replies)
            except Exception:
                logger.error("Tracker: %s scan failed", platform, exc_info=True)

        # 4. 새 답글을 DB에 저장하고 이벤트 발행
        new_replies = self._save_new_replies(all_replies)

        if bus and new_replies:
            for reply in new_replies:
                await bus.emit(
                    Event(
                        "reply.detected",
                        {
                            "comment_id": reply.comment_id,
                            "platform": reply.platform,
                            "post_url": reply.post_url,
                            "author": reply.author,
                            "body": reply.body,
                            "post_title": reply.post_title,
                        },
                    )
                )

        logger.info(
            "Tracker scan complete: %d posts scanned, %d new replies detected",
            sum(len(p) for p in post_groups.values()),
            len(new_replies),
        )
        return new_replies

    async def _scan_platform(
        self, platform: str, posts: list[dict[str, str]]
    ) -> list[DetectedReply]:
        """Detect replies on posts for a specific platform."""
        cls = get_adapter_class(platform)
        my_username = self._get_username(platform)

        # Dev.to는 DEVTO_USERNAME 없으면 API로 자동 조회
        if not my_username and platform == "devto":
            my_username = await self._resolve_devto_username()

        if not my_username:
            logger.warning("Tracker: %s username unknown — cannot detect replies", platform)
            return []

        replies: list[DetectedReply] = []
        adapter = cls()
        async with adapter:
            for post_info in posts:
                try:
                    post_id = post_info["post_id"]
                    if not post_id:
                        continue

                    comments = await adapter.get_comments(post_id, limit=100)
                    post_title = ""

                    # 내 댓글 ID 수집
                    my_comment_ids: set[str] = set()
                    for c in comments:
                        if c.author.lower() == my_username.lower():
                            my_comment_ids.add(c.id)

                    if not my_comment_ids:
                        continue

                    # 내 댓글에 대한 답글 찾기
                    for c in comments:
                        if (
                            c.parent_id
                            and c.parent_id in my_comment_ids
                            and c.author.lower() != my_username.lower()
                        ):
                            replies.append(
                                DetectedReply(
                                    comment_id=c.id,
                                    platform=platform,
                                    post_url=post_info["post_url"],
                                    parent_comment_id=c.parent_id,
                                    author=c.author,
                                    body=c.body,
                                    post_title=post_title,
                                )
                            )

                except Exception:
                    logger.error(
                        "Tracker: 게시글 스캔 실패 (%s: %s)",
                        platform,
                        post_info.get("post_url", "?"),
                        exc_info=True,
                    )

        return replies

    def _save_new_replies(self, replies: list[DetectedReply]) -> list[DetectedReply]:
        """Save only new replies to DB. Skip already detected ones."""
        if not replies:
            return []

        conn = _get_db(self._db_path)
        ensure_replies_table(conn)
        new: list[DetectedReply] = []
        try:
            now = datetime.now(timezone.utc).isoformat()
            for r in replies:
                try:
                    conn.execute(
                        "INSERT INTO replies (comment_id, platform, post_url, parent_comment_id, author, body, detected_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            r.comment_id,
                            r.platform,
                            r.post_url,
                            r.parent_comment_id,
                            r.author,
                            r.body,
                            now,
                        ),
                    )
                    new.append(r)
                except sqlite3.IntegrityError:
                    pass  # 이미 감지된 답글
            conn.commit()
        finally:
            conn.close()

        return new

    def _get_username(self, platform: str) -> str:
        """Look up the gwanjong username for a given platform."""
        if platform in self._usernames:
            return self._usernames[platform]

        import os

        username_map: dict[str, str] = {
            "devto": os.getenv("DEVTO_USERNAME", ""),
            "bluesky": os.getenv("BLUESKY_HANDLE", ""),
            "twitter": os.getenv("TWITTER_USERNAME", ""),
            "reddit": os.getenv("REDDIT_USERNAME", ""),
            "github_discussions": os.getenv("GITHUB_USERNAME", ""),
            "discourse": os.getenv("DISCOURSE_API_USERNAME", ""),
        }
        username = username_map.get(platform, "")
        if username:
            self._usernames[platform] = username
        return username

    async def _resolve_devto_username(self) -> str:
        """Resolve username via the Dev.to /users/me API."""
        if "devto" in self._usernames:
            return self._usernames["devto"]

        import os

        import httpx

        api_key = os.getenv("DEVTO_API_KEY", "")
        if not api_key:
            return ""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://dev.to/api/users/me",
                    headers={"api-key": api_key},
                )
                resp.raise_for_status()
                username = resp.json().get("username", "")
                if username:
                    self._usernames["devto"] = username
                    logger.info("Dev.to username resolved: %s", username)
                return username
        except Exception:
            logger.warning("Failed to resolve Dev.to username", exc_info=True)
            return ""

    def get_pending_replies(self, platform: str | None = None) -> list[dict[str, Any]]:
        """Retrieve replies that have not been responded to yet."""
        conn = _get_db(self._db_path)
        ensure_replies_table(conn)
        try:
            if platform:
                rows = conn.execute(
                    "SELECT * FROM replies WHERE responded = 0 AND platform = ? ORDER BY id DESC",
                    (platform,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM replies WHERE responded = 0 ORDER BY id DESC",
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_responded(self, comment_id: str) -> None:
        """Mark a reply as responded."""
        conn = _get_db(self._db_path)
        ensure_replies_table(conn)
        try:
            conn.execute(
                "UPDATE replies SET responded = 1 WHERE comment_id = ?",
                (comment_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def get_stats(self) -> dict[str, Any]:
        """Reply tracking statistics."""
        conn = _get_db(self._db_path)
        ensure_replies_table(conn)
        try:
            total = conn.execute("SELECT COUNT(*) as cnt FROM replies").fetchone()["cnt"]
            pending = conn.execute(
                "SELECT COUNT(*) as cnt FROM replies WHERE responded = 0"
            ).fetchone()["cnt"]
            by_platform = conn.execute(
                "SELECT platform, COUNT(*) as cnt FROM replies GROUP BY platform"
            ).fetchall()
            return {
                "total_replies": total,
                "pending": pending,
                "responded": total - pending,
                "by_platform": {r["platform"]: r["cnt"] for r in by_platform},
            }
        finally:
            conn.close()
