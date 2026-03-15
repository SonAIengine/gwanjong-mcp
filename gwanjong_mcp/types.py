"""gwanjong-mcp 내부 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Opportunity:
    """scout가 발견한 참여 기회."""

    id: str
    platform: str
    post_id: str
    title: str
    url: str
    relevance: float
    comments_count: int
    reason: str
    suggested_actions: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DraftContext:
    """draft가 수집한 게시글 맥락."""

    opportunity_id: str
    platform: str
    title: str
    body_summary: str
    post_id: str = ""
    top_comments: list[str] = field(default_factory=list)
    tone: str = ""
    suggested_approach: str = "comment"
    avoid: list[str] = field(default_factory=list)


@dataclass
class ActionRecord:
    """strike 실행 이력."""

    opportunity_id: str
    action: str
    platform: str
    url: str
    timestamp: str
    post_id: str = ""


# ── Phase 1: Campaign ──


@dataclass
class Campaign:
    """마케팅 캠페인."""

    id: str
    name: str
    objective: str  # "awareness" | "engagement" | "conversion"
    topics: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    icp: str = ""  # ideal customer profile
    cta: str = ""  # call to action
    kpi_target: dict[str, float] = field(default_factory=dict)
    start_date: str = ""
    end_date: str | None = None
    status: str = "active"  # "draft" | "active" | "paused" | "completed"
    created_at: str = ""


# ── Phase 2: Conversion ──


@dataclass
class ConversionEvent:
    """UTM 전환 이벤트."""

    id: str
    campaign_id: str
    source: str  # platform
    medium: str  # action type
    action_id: int | None = None
    url: str = ""
    event_type: str = "click"  # "click" | "star" | "install"
    created_at: str = ""


# ── Phase 3: Asset + MessageFrame ──


@dataclass
class Asset:
    """재사용 가능한 콘텐츠 에셋."""

    id: str
    campaign_id: str | None = None  # None = global
    asset_type: str = ""  # "hook" | "cta" | "snippet" | "template"
    platform: str = ""  # "" = cross-platform
    content: str = ""
    tags: list[str] = field(default_factory=list)
    usage_count: int = 0
    last_used: str = ""
    created_at: str = ""


@dataclass
class MessageFrame:
    """ICP별 메시지 프레임워크."""

    id: str
    campaign_id: str
    persona_segment: str  # "senior-backend" | "startup-founder"
    value_prop: str = ""
    proof_points: list[str] = field(default_factory=list)
    objections: dict[str, str] = field(default_factory=dict)
    hooks: list[str] = field(default_factory=list)
    created_at: str = ""


# ── Phase 4: Schedule ──


@dataclass
class ScheduleItem:
    """예약 발행 항목."""

    id: str
    campaign_id: str
    platform: str
    action: str  # "post" | "comment"
    content: str
    scheduled_at: str  # ISO datetime
    status: str = "pending"  # "pending" | "published" | "failed" | "cancelled"
    asset_ids: list[str] = field(default_factory=list)
    published_at: str = ""
    error: str = ""
    created_at: str = ""
