"""Autonomous loop engine — scout->draft->generate->strike cycle. Depends only on pipeline + events."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from . import pipeline
from .approval import ApprovalQueue
from .events import Blocked, Event, EventBus
from .llm import CommentGenerator
from .memory import Memory
from .safety import Safety
from .tracker import Tracker
from .types import DraftContext, Opportunity

logger = logging.getLogger(__name__)


@dataclass
class CycleConfig:
    """Cycle configuration."""

    topics: list[str] = field(default_factory=lambda: ["MCP"])
    max_actions_per_cycle: int = 3
    platforms: list[str] | None = None
    scout_limit: int = 10
    min_relevance: float = 0.3
    require_approval: bool = False
    track_replies: bool = True
    dry_run: bool = False
    campaign_id: str = ""


@dataclass
class CycleResult:
    """Cycle execution result."""

    topic: str
    scanned: int = 0
    opportunities: int = 0
    actions_attempted: int = 0
    actions_queued: int = 0
    actions_succeeded: int = 0
    actions_blocked: int = 0
    replies_detected: int = 0
    errors: list[str] = field(default_factory=list)


class AutonomousLoop:
    """Autonomous cycle engine. Depends only on pipeline + EventBus.

    Plugins like safety and memory intervene automatically via EventBus.
    """

    def __init__(
        self,
        bus: EventBus,
        llm: CommentGenerator | None = None,
        config: CycleConfig | None = None,
        approval_queue: ApprovalQueue | None = None,
        safety: Safety | None = None,
    ) -> None:
        self.bus = bus
        self.llm = llm or CommentGenerator()
        self.config = config or CycleConfig()
        self.approval_queue = approval_queue or ApprovalQueue()
        self.safety = safety
        self._running = False

    async def run_cycle(self, topic: str) -> CycleResult:
        """One cycle: scout -> draft -> generate -> strike.

        If safety/memory are attached to the bus, they automatically:
        - Check rate limits/content on strike.before (may block)
        - Record seen_posts on scout.done
        - Log action history + rate_log on strike.after
        """
        result = CycleResult(topic=topic)

        # 0. 차단 플랫폼 제외 (불필요한 API 호출 방지)
        effective_platforms = self.config.platforms
        if self.safety:
            banned = self.safety.get_banned_platforms()
            if banned:
                base = effective_platforms or []
                if base:
                    effective_platforms = [p for p in base if p not in banned]
                else:
                    effective_platforms = None  # 전체에서 제외는 scout 내부에서 처리 불가 → 로그만
                logger.info("차단 플랫폼 제외: %s", banned)

        # 1. scout
        try:
            opportunities, response = await pipeline.scout(
                topic,
                platforms=effective_platforms,
                limit=self.config.scout_limit,
                bus=self.bus,
                campaign_id=self.config.campaign_id,
            )
        except Exception as e:
            result.errors.append(f"scout 실패: {e}")
            logger.error("scout 실패", exc_info=True)
            return result

        result.scanned = response.get("total_scanned", 0)
        result.opportunities = len(opportunities)

        if not opportunities:
            logger.info("topic='%s': 기회 없음", topic)
            return result

        # 2. 이미 활동한 게시글 필터링
        memory = Memory()
        filtered = memory.filter_unseen(opportunities)
        if len(filtered) < len(opportunities):
            logger.info(
                "중복 필터링: %d → %d (이미 활동한 %d건 제외)",
                len(opportunities),
                len(filtered),
                len(opportunities) - len(filtered),
            )

        # 3. 최소 관련성 점수 필터링
        sorted_opps = sorted(
            filtered.values(),
            key=lambda o: o.relevance,
            reverse=True,
        )
        sorted_opps = [o for o in sorted_opps if o.relevance >= self.config.min_relevance]

        if not sorted_opps:
            logger.info("topic='%s': 관련성 %.1f 이상 기회 없음", topic, self.config.min_relevance)
            return result

        for opp in sorted_opps[: self.config.max_actions_per_cycle]:
            await self._process_opportunity(opp, result)

        # 5. 실패한 승인 항목 자동 재시도
        try:
            failed = self.approval_queue.get_failed()
            for item in failed:
                try:
                    retry_result = await self.approval_queue.retry_failed(item["id"], bus=self.bus)
                    logger.info(
                        "자동 재시도 성공: #%d → %s",
                        item["id"],
                        retry_result.get("queue_status", "unknown"),
                    )
                except Exception as e:
                    if "cooldown" in str(e).lower() or "limit" in str(e).lower():
                        logger.debug("재시도 대기: #%d — %s", item["id"], e)
                        break  # 쿨다운이면 나머지도 안 됨
                    logger.warning("재시도 실패: #%d — %s", item["id"], e)
        except Exception as e:
            logger.debug("승인 재시도 처리 에러: %s", e)

        # 6. 예약 발행 처리
        try:
            from .scheduler import Scheduler

            scheduler = Scheduler()
            due_results = await scheduler.process_due(bus=self.bus)
            if due_results:
                logger.info("예약 발행 %d건 처리", len(due_results))
        except Exception as e:
            result.errors.append(f"scheduler 처리 실패: {e}")
            logger.error("scheduler 처리 실패", exc_info=True)

        # 7. 답글 스캔 + 자동 대댓글 (track_replies 활성화 시)
        if self.config.track_replies:
            try:
                tracker = Tracker()
                new_replies = await tracker.scan(
                    bus=self.bus,
                    platforms=self.config.platforms,
                )
                result.replies_detected = len(new_replies)
                for reply in new_replies:
                    logger.info(
                        "새 답글 감지: %s @%s → '%s'",
                        reply.platform,
                        reply.author,
                        reply.body[:80],
                    )
                    # 자동 대댓글
                    if not self.config.dry_run:
                        await self._reply_to_reply(reply, tracker, result)
            except Exception as e:
                result.errors.append(f"reply scan 실패: {e}")
                logger.error("reply scan 실패", exc_info=True)

        logger.info(
            "Cycle done: topic='%s', scanned=%d, attempted=%d, queued=%d, succeeded=%d, blocked=%d, replies=%d",
            topic,
            result.scanned,
            result.actions_attempted,
            result.actions_queued,
            result.actions_succeeded,
            result.actions_blocked,
            result.replies_detected,
        )
        return result

    async def _process_opportunity(self, opp: Opportunity, result: CycleResult) -> None:
        """Process a single opportunity: draft -> generate -> strike."""
        # 승인 큐에 이미 있는 글이면 건너뛰기
        try:
            pending = self.approval_queue.get_pending()
            failed = self.approval_queue.get_failed()
            queued_urls = {item["post_url"] for item in pending + failed if item.get("post_url")}
            if opp.url in queued_urls:
                logger.info("승인 큐에 이미 존재: %s — 건너뜀", opp.title[:50])
                return
        except Exception:
            logger.debug("승인 큐 중복 체크 실패", exc_info=True)

        try:
            # draft (1회 재시도)
            ctx, _draft_response = await self._retry(
                lambda: pipeline.draft(opp, bus=self.bus),
                label=f"draft({opp.id})",
            )
        except Exception as e:
            result.errors.append(f"draft 실패 ({opp.id}): {e}")
            logger.error("draft 실패: %s", opp.id, exc_info=True)
            return

        if self.config.dry_run:
            logger.info("Dry-run: strike 생략 (%s %s)", opp.platform, opp.title[:50])
            return

        # generate
        try:
            content = await self.llm.generate(ctx)
        except Exception as e:
            result.errors.append(f"LLM 생성 실패 ({opp.id}): {e}")
            logger.error("LLM 생성 실패: %s", opp.id, exc_info=True)
            return

        # 자율 모드에서는 comment만 허용 (post는 승인 모드에서만)
        action = ctx.suggested_approach
        if not self.config.require_approval and action == "post":
            action = "comment"
            logger.info("자율 모드: post → comment 변환 (%s)", opp.title[:50])

        # approval 체크
        if self.config.require_approval:
            item = self.approval_queue.enqueue(
                topic=result.topic,
                opportunity=opp,
                context=ctx,
                action=action,
                content=content,
            )
            result.actions_queued += 1
            await self.bus.emit(
                Event(
                    "approval.queued",
                    {
                        "item_id": item.id,
                        "topic": result.topic,
                        "platform": opp.platform,
                        "action": action,
                        "title": opp.title,
                    },
                )
            )
            logger.info("승인 대기 등록: #%d %s — %s", item.id, opp.platform, opp.title[:50])
            return

        # strike (1회 재시도)
        result.actions_attempted += 1
        try:
            record, response = await self._retry(
                lambda _ctx=ctx, _act=action, _cont=content: pipeline.strike(
                    _ctx,
                    _act,
                    _cont,
                    bus=self.bus,
                    campaign_id=self.config.campaign_id,
                ),
                label=f"strike({opp.id})",
            )
            if response.get("status") == "posted":
                result.actions_succeeded += 1
                logger.info(
                    "Strike 성공: %s %s → %s",
                    opp.platform,
                    action,
                    response.get("url", ""),
                )
            else:
                error_msg = response.get("error", "unknown")
                result.errors.append(f"strike 실패 ({opp.id}): {error_msg}")
                # 실패 이벤트 → Safety가 연속 실패 추적
                await self.bus.emit(
                    Event(
                        "strike.failed",
                        {
                            "platform": opp.platform,
                            "action": action,
                            "error": error_msg,
                        },
                    )
                )
        except Blocked as e:
            result.actions_blocked += 1
            logger.info("Strike 차단: %s", e)
        except Exception as e:
            result.errors.append(f"strike 에러 ({opp.id}): {e}")
            logger.error("strike 에러: %s", opp.id, exc_info=True)

    async def run_daemon(
        self,
        interval_hours: float = 4.0,
        max_cycles: int | None = None,
    ) -> None:
        """Daemon mode: run cycles periodically.

        Args:
            interval_hours: Interval between cycles (in hours)
            max_cycles: Maximum number of cycles (None for unlimited)
        """
        self._running = True
        cycle_count = 0

        logger.info(
            "Daemon started: topics=%s, interval=%.1fh",
            self.config.topics,
            interval_hours,
        )

        while self._running:
            for topic in self.config.topics:
                if not self._running:
                    break
                try:
                    result = await self.run_cycle(topic)
                    logger.info(
                        "Cycle %d/%s: %d/%d succeeded",
                        cycle_count + 1,
                        topic,
                        result.actions_succeeded,
                        result.actions_attempted,
                    )
                except Exception:
                    logger.error("Cycle 에러: topic=%s", topic, exc_info=True)

            cycle_count += 1
            if max_cycles and cycle_count >= max_cycles:
                logger.info("Max cycles (%d) reached, stopping", max_cycles)
                break

            logger.info("다음 사이클까지 %.1f시간 대기", interval_hours)
            try:
                await asyncio.sleep(interval_hours * 3600)
            except asyncio.CancelledError:
                logger.info("Daemon cancelled")
                break

        self._running = False
        logger.info("Daemon stopped")

    async def _reply_to_reply(
        self,
        reply: Any,
        tracker: Tracker,
        result: CycleResult,
    ) -> None:
        """Generate and post a reply to someone who replied to our comment."""
        from .storage import get_db

        conn = get_db()
        try:
            row = conn.execute(
                "SELECT content, post_id FROM actions WHERE platform = ? AND action = 'comment' AND post_url LIKE ? ORDER BY id DESC LIMIT 1",
                (reply.platform, f"%{reply.post_url.split('#')[0]}%"),
            ).fetchone()
            my_original = row["content"] if row else ""
            post_id = row["post_id"] if row else ""
        finally:
            conn.close()

        if not post_id:
            logger.warning("대댓글 건너뜀: post_id를 찾을 수 없음 (%s)", reply.post_url)
            return

        # 대댓글용 DraftContext 구성
        ctx = DraftContext(
            opportunity_id=f"reply_{reply.comment_id}",
            platform=reply.platform,
            title=reply.post_title or "",
            body_summary=f"내가 쓴 댓글:\n{my_original}\n\n@{reply.author}의 답글:\n{reply.body}",
            post_id=post_id,
            top_comments=[],
            tone="conversational",
            suggested_approach="comment",
        )

        # LLM으로 대댓글 생성
        try:
            content = await self.llm.generate(ctx)
        except Exception as e:
            logger.warning("대댓글 생성 실패: %s — %s", reply.comment_id, e)
            return

        # 발행
        try:
            record, response = await pipeline.strike(
                ctx,
                "comment",
                content,
                bus=self.bus,
                campaign_id=self.config.campaign_id,
            )
            if response.get("status") == "posted":
                tracker.mark_responded(reply.comment_id)
                logger.info(
                    "대댓글 발행: %s @%s → %s",
                    reply.platform,
                    reply.author,
                    response.get("url", ""),
                )
            else:
                logger.warning("대댓글 발행 실패: %s", response.get("error", ""))
        except Blocked as e:
            logger.info("대댓글 차단 (rate limit): %s", e)
        except Exception as e:
            logger.warning("대댓글 에러: %s — %s", reply.comment_id, e)

    @staticmethod
    async def _retry(fn, *, label: str = "", max_retries: int = 1, base_delay: float = 3.0):
        """Retry an async callable with exponential backoff. Re-raises on final failure."""
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await fn()
            except (Blocked, ValueError):
                raise  # 의도적 차단이나 로직 에러는 재시도 안 함
            except Exception as e:
                last_exc = e
                if attempt < max_retries:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        "%s 실패 (시도 %d/%d), %.0f초 후 재시도: %s",
                        label,
                        attempt + 1,
                        max_retries + 1,
                        delay,
                        e,
                    )
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    def stop(self) -> None:
        """Request daemon stop."""
        self._running = False
