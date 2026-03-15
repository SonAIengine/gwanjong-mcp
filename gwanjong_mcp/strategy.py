"""Strategy engine — LLM-based weekly planning + auto-approval for low-risk actions."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .campaign import CampaignManager
from .measure import Measurement
from .scheduler import Scheduler
from .storage import DB_PATH
from .types import ScheduleItem

logger = logging.getLogger(__name__)


class StrategyEngine:
    """Weekly plan generation + low-risk auto-scheduling."""

    def __init__(
        self,
        db_path: Path = DB_PATH,
        llm: Any | None = None,
    ) -> None:
        self._db_path = db_path
        self.campaigns = CampaignManager(db_path)
        self.measurement = Measurement(db_path)
        self.scheduler = Scheduler(db_path)
        self._llm = llm

    async def generate_weekly_plan(self, campaign_id: str) -> dict[str, Any]:
        """Generate a weekly plan based on last week's performance."""
        camp = self.campaigns.get(campaign_id)
        if not camp:
            return {"error": f"캠페인 '{campaign_id}' 없음"}

        report = self.measurement.weekly_report(campaign_id)
        best = self.measurement.best_performing(campaign_id, "actions")

        # 성과 기반 제안 생성 (규칙 기반, LLM 없이도 동작)
        suggestions = []
        total = report.get("total_actions", 0)

        # 플랫폼별 활동 분배 제안
        platform_allocation = self.suggest_platform_allocation(campaign_id)
        topic_rotation = self.suggest_topic_rotation(campaign_id)

        if total == 0:
            suggestions.append("지난주 활동 없음 — 최소 일 1건 활동 권장")
        elif total < 7:
            suggestions.append(f"주간 활동 {total}건 — 목표 대비 부족, 빈도 증가 권장")
        else:
            suggestions.append(f"주간 활동 {total}건 — 양호")

        # 전환 대비 활동 효율
        conversions = report.get("total_conversions", 0)
        if total > 0 and conversions == 0:
            suggestions.append("전환 0건 — CTA 또는 UTM 태깅 점검 필요")

        # 최고 성과 채널 강화 제안
        if best:
            top = best[0]
            suggestions.append(
                f"최고 성과: {top['platform']}/{top['action']} ({top['count']}건) — 집중 권장"
            )

        plan = {
            "campaign_id": campaign_id,
            "campaign_name": camp.name,
            "period": report.get("period", {}),
            "last_week_summary": {
                "total_actions": total,
                "total_conversions": conversions,
                "by_platform": report.get("by_platform", {}),
                "by_action": report.get("by_action", {}),
            },
            "suggestions": suggestions,
            "platform_allocation": platform_allocation,
            "topic_rotation": topic_rotation,
            "active_experiments": report.get("active_experiments", []),
        }

        # LLM이 있으면 추가 인사이트 생성
        if self._llm:
            try:
                from .types import DraftContext

                ctx = DraftContext(
                    opportunity_id="strategy",
                    platform="",
                    title=f"Weekly strategy for {camp.name}",
                    body_summary=str(plan),
                    suggested_approach="post",
                )
                insight = await self._llm.generate(ctx)
                plan["llm_insight"] = insight
            except Exception as e:
                logger.warning("LLM insight 생성 실패: %s", e)

        return plan

    def auto_approve_low_risk(self, plan: dict[str, Any]) -> list[ScheduleItem]:
        """Auto-schedule low-risk actions from a plan."""
        items = []
        campaign_id = plan.get("campaign_id", "")
        allocation = plan.get("platform_allocation", {})
        topics = plan.get("topic_rotation", [])

        if not campaign_id or not allocation:
            return items

        now = datetime.now(timezone.utc)

        # 댓글만 자동 승인 (post는 수동 승인 필요)
        day_offset = 0
        for platform, count in allocation.items():
            for i in range(min(count, 2)):  # 플랫폼당 최대 2건 자동 스케줄
                scheduled_at = (now + timedelta(days=day_offset, hours=9 + i * 4)).isoformat()
                topic = topics[i % len(topics)] if topics else "general"
                item = self.scheduler.add(
                    {
                        "campaign_id": campaign_id,
                        "platform": platform,
                        "action": "comment",
                        "content": f"[auto-plan] topic: {topic}",
                        "scheduled_at": scheduled_at,
                    }
                )
                items.append(item)
            day_offset += 1

        logger.info("Auto-scheduled %d low-risk items for %s", len(items), campaign_id)
        return items

    def suggest_topic_rotation(self, campaign_id: str) -> list[str]:
        """Suggest topic rotation based on campaign config and past activity."""
        camp = self.campaigns.get(campaign_id)
        if not camp or not camp.topics:
            return []

        # 간단한 로테이션: 기존 topics를 순환
        return list(camp.topics)

    def suggest_platform_allocation(self, campaign_id: str) -> dict[str, int]:
        """Suggest per-platform activity counts for next week."""
        camp = self.campaigns.get(campaign_id)
        if not camp:
            return {}

        report = self.measurement.weekly_report(campaign_id)
        by_platform = report.get("by_platform", {})
        platforms = camp.platforms or list(by_platform.keys())

        if not platforms:
            return {}

        # 기본 할당: 플랫폼당 주 3건, 성과 좋은 곳은 +1
        allocation = {}
        best_platform = max(by_platform, key=by_platform.get, default=None) if by_platform else None

        for p in platforms:
            base = 3
            if p == best_platform:
                base += 1
            allocation[p] = base

        return allocation
