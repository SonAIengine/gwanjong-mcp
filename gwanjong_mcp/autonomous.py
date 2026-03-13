"""자율 루프 엔진 — scout→draft→generate→strike 사이클. pipeline + events만 의존."""

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
from .tracker import Tracker
from .types import Opportunity

logger = logging.getLogger(__name__)


@dataclass
class CycleConfig:
    """사이클 설정."""

    topics: list[str] = field(default_factory=lambda: ["MCP"])
    max_actions_per_cycle: int = 3
    platforms: list[str] | None = None
    scout_limit: int = 10
    min_relevance: float = 0.3
    require_approval: bool = False
    track_replies: bool = True


@dataclass
class CycleResult:
    """사이클 실행 결과."""

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
    """자율 사이클 엔진. pipeline + EventBus만 의존.

    safety, memory 등은 EventBus를 통해 자동 개입.
    """

    def __init__(
        self,
        bus: EventBus,
        llm: CommentGenerator | None = None,
        config: CycleConfig | None = None,
        approval_queue: ApprovalQueue | None = None,
    ) -> None:
        self.bus = bus
        self.llm = llm or CommentGenerator()
        self.config = config or CycleConfig()
        self.approval_queue = approval_queue or ApprovalQueue()
        self._running = False

    async def run_cycle(self, topic: str) -> CycleResult:
        """한 사이클: scout → draft → generate → strike.

        safety/memory가 bus에 붙어 있으면 자동으로:
        - strike.before에서 rate limit/content 체크 (차단 가능)
        - scout.done에서 seen_posts 기록
        - strike.after에서 이력 기록 + rate_log 기록
        """
        result = CycleResult(topic=topic)

        # 1. scout
        try:
            opportunities, response = await pipeline.scout(
                topic,
                platforms=self.config.platforms,
                limit=self.config.scout_limit,
                bus=self.bus,
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
                len(opportunities), len(filtered),
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

        # 5. 답글 스캔 (track_replies 활성화 시)
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
                        reply.platform, reply.author, reply.body[:80],
                    )
            except Exception as e:
                result.errors.append(f"reply scan 실패: {e}")
                logger.error("reply scan 실패", exc_info=True)

        logger.info(
            "Cycle done: topic='%s', scanned=%d, attempted=%d, queued=%d, succeeded=%d, blocked=%d, replies=%d",
            topic, result.scanned, result.actions_attempted,
            result.actions_queued, result.actions_succeeded,
            result.actions_blocked, result.replies_detected,
        )
        return result

    async def _process_opportunity(
        self, opp: Opportunity, result: CycleResult
    ) -> None:
        """단일 기회 처리: draft → generate → strike."""
        try:
            # draft
            ctx, draft_response = await pipeline.draft(opp, bus=self.bus)
        except Exception as e:
            result.errors.append(f"draft 실패 ({opp.id}): {e}")
            logger.error("draft 실패: %s", opp.id, exc_info=True)
            return

        # generate
        try:
            content = await self.llm.generate(ctx)
        except Exception as e:
            result.errors.append(f"LLM 생성 실패 ({opp.id}): {e}")
            logger.error("LLM 생성 실패: %s", opp.id, exc_info=True)
            return

        # approval 체크
        if self.config.require_approval:
            item = self.approval_queue.enqueue(
                topic=result.topic,
                opportunity=opp,
                context=ctx,
                action=ctx.suggested_approach,
                content=content,
            )
            result.actions_queued += 1
            await self.bus.emit(Event("approval.queued", {
                "item_id": item.id,
                "topic": result.topic,
                "platform": opp.platform,
                "action": ctx.suggested_approach,
                "title": opp.title,
            }))
            logger.info("승인 대기 등록: #%d %s — %s", item.id, opp.platform, opp.title[:50])
            return

        # strike
        result.actions_attempted += 1
        try:
            # context의 opportunity_id를 실제 post_id로 교체
            ctx.opportunity_id = opp.post_id
            record, response = await pipeline.strike(
                ctx, ctx.suggested_approach, content, bus=self.bus,
            )
            if response.get("status") == "posted":
                result.actions_succeeded += 1
                logger.info("Strike 성공: %s %s → %s", opp.platform, ctx.suggested_approach, response.get("url", ""))
            else:
                result.errors.append(f"strike 실패 ({opp.id}): {response.get('error', 'unknown')}")
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
        """데몬 모드: 주기적으로 사이클 실행.

        Args:
            interval_hours: 사이클 간격 (시간)
            max_cycles: 최대 사이클 수 (None이면 무한)
        """
        self._running = True
        cycle_count = 0

        logger.info(
            "Daemon started: topics=%s, interval=%.1fh",
            self.config.topics, interval_hours,
        )

        while self._running:
            for topic in self.config.topics:
                if not self._running:
                    break
                try:
                    result = await self.run_cycle(topic)
                    logger.info(
                        "Cycle %d/%s: %d/%d succeeded",
                        cycle_count + 1, topic,
                        result.actions_succeeded, result.actions_attempted,
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

    def stop(self) -> None:
        """데몬 정지 요청."""
        self._running = False
