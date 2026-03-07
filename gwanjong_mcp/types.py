"""gwanjong-mcp 내부 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Opportunity:
    """scout가 발견한 홍보 기회."""

    id: str
    platform: str
    post_id: str
    title: str
    url: str
    relevance: float
    comments_count: int
    reason: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DraftContext:
    """draft가 수집한 게시글 맥락."""

    opportunity_id: str
    platform: str
    title: str
    body_summary: str
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
