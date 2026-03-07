"""scout/draft/strike 파이프라인 로직."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from devhub import Hub
from devhub.types import Post

from .types import ActionRecord, DraftContext, Opportunity

logger = logging.getLogger(__name__)


def _score_relevance(post: Post, topic: str) -> float:
    """게시글의 주제 관련성 점수 (0.0 ~ 1.0)."""
    score = 0.0
    topic_lower = topic.lower()
    words = topic_lower.split()

    # 제목 매칭
    title_lower = post.title.lower()
    for word in words:
        if word in title_lower:
            score += 0.3

    # 본문 매칭
    body_lower = post.body.lower()
    for word in words:
        if word in body_lower:
            score += 0.1

    # 태그 매칭
    for tag in post.tags:
        if tag.lower() in topic_lower or any(w in tag.lower() for w in words):
            score += 0.2

    # 활발한 토론 보너스 (댓글 많을수록)
    if post.comments_count > 20:
        score += 0.15
    elif post.comments_count > 5:
        score += 0.1

    # 인기도 보너스
    if post.likes > 50:
        score += 0.1
    elif post.likes > 10:
        score += 0.05

    return min(score, 1.0)


def _generate_reason(post: Post, topic: str, score: float) -> str:
    """기회 선정 이유를 생성."""
    parts: list[str] = []
    if score >= 0.7:
        parts.append("주제 직접 관련")
    elif score >= 0.4:
        parts.append("주제 간접 관련")
    if post.comments_count > 20:
        parts.append(f"활발한 토론 ({post.comments_count} comments)")
    elif post.comments_count > 5:
        parts.append(f"적당한 토론 ({post.comments_count} comments)")
    if post.likes > 50:
        parts.append(f"인기 게시글 ({post.likes} likes)")
    return ", ".join(parts) if parts else "잠재적 기회"


async def scout(
    topic: str,
    platforms: list[str] | None = None,
    limit: int = 5,
) -> tuple[dict[str, Opportunity], dict[str, Any]]:
    """정찰: trending + search → 점수화 → 상위 N개 기회 반환.

    Returns:
        (opportunities_dict, compressed_response)
    """
    async with Hub.from_env() as hub:
        # 플랫폼 필터링
        if platforms:
            hub.adapters = [a for a in hub.adapters if a.platform in platforms]

        if not hub.adapters:
            return {}, {
                "opportunities": [],
                "total_scanned": 0,
                "summary": "활성 플랫폼 없음. gwanjong_setup으로 플랫폼을 먼저 설정하세요.",
            }

        # trending + search 병렬 실행
        trending, searched = await asyncio.gather(
            hub.get_trending(limit=20),
            hub.search(topic, limit=20),
        )

    # 중복 제거 (URL 기준)
    seen: set[str] = set()
    all_posts: list[Post] = []
    for post in trending + searched:
        if post.url not in seen:
            seen.add(post.url)
            all_posts.append(post)

    # 점수화 및 정렬
    scored = [(post, _score_relevance(post, topic)) for post in all_posts]
    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:limit]

    # Opportunity 생성
    opportunities: dict[str, Opportunity] = {}
    compressed: list[dict[str, Any]] = []
    for i, (post, score) in enumerate(top):
        opp_id = f"opp_{i}"
        opp = Opportunity(
            id=opp_id,
            platform=post.platform,
            post_id=post.id,
            title=post.title[:80] if post.title else "(no title)",
            url=post.url,
            relevance=round(score, 2),
            comments_count=post.comments_count,
            reason=_generate_reason(post, topic, score),
            raw={"author": post.author, "likes": post.likes, "tags": post.tags},
        )
        opportunities[opp_id] = opp
        compressed.append({
            "id": opp_id,
            "platform": opp.platform,
            "title": opp.title,
            "relevance": opp.relevance,
            "comments": opp.comments_count,
            "reason": opp.reason,
        })

    platforms_found = len({o.platform for o in opportunities.values()})
    response = {
        "opportunities": compressed,
        "total_scanned": len(all_posts),
        "summary": f"{platforms_found}개 플랫폼에서 {len(compressed)}건의 기회 발견",
    }

    return opportunities, response


async def draft(
    opportunity: Opportunity,
) -> tuple[DraftContext, dict[str, Any]]:
    """초안 준비: 게시글 + 댓글 트리 + 분위기 분석.

    Returns:
        (draft_context, compressed_response)
    """
    from devhub.bluesky import Bluesky
    from devhub.devto import DevTo
    from devhub.reddit import Reddit
    from devhub.twitter import Twitter

    adapter_map = {
        "devto": DevTo,
        "bluesky": Bluesky,
        "twitter": Twitter,
        "reddit": Reddit,
    }

    cls = adapter_map.get(opportunity.platform)
    if cls is None:
        raise ValueError(f"지원하지 않는 플랫폼: {opportunity.platform}")

    adapter = cls()
    async with adapter:
        post = await adapter.get_post(opportunity.post_id)
        comments = await adapter.get_comments(opportunity.post_id, limit=20)

    # 분위기 분석 (간단 휴리스틱)
    comment_bodies = [c.body for c in comments]
    tone = _analyze_tone(comment_bodies)
    approach = _suggest_approach(opportunity, len(comments))
    avoid = _check_avoid(opportunity)

    ctx = DraftContext(
        opportunity_id=opportunity.id,
        platform=opportunity.platform,
        title=post.title,
        body_summary=post.body[:500] if post.body else "",
        top_comments=[c.body[:200] for c in comments[:5]],
        tone=tone,
        suggested_approach=approach,
        avoid=avoid,
    )

    response = {
        "title": ctx.title,
        "body_summary": ctx.body_summary,
        "comment_count": len(comments),
        "top_comments": ctx.top_comments,
        "tone": ctx.tone,
        "suggested_approach": ctx.suggested_approach,
        "avoid": ctx.avoid,
    }

    return ctx, response


async def strike(
    context: DraftContext,
    action: str,
    content: str,
) -> tuple[ActionRecord, dict[str, Any]]:
    """실행: 댓글/게시글/교차게시 작성.

    Returns:
        (action_record, compressed_response)
    """
    from devhub.bluesky import Bluesky
    from devhub.devto import DevTo
    from devhub.reddit import Reddit
    from devhub.twitter import Twitter

    adapter_map = {
        "devto": DevTo,
        "bluesky": Bluesky,
        "twitter": Twitter,
        "reddit": Reddit,
    }

    cls = adapter_map.get(context.platform)
    if cls is None:
        raise ValueError(f"지원하지 않는 플랫폼: {context.platform}")

    adapter = cls()
    async with adapter:
        if action == "comment":
            # opportunity의 post_id가 필요 — context에서 역추적
            # DraftContext에는 post_id가 없으므로 opportunity_id로 state에서 가져와야 함
            # 여기선 server.py에서 post_id를 넘겨받는 구조
            result = await adapter.write_comment(
                context.opportunity_id,  # server.py에서 실제 post_id로 교체
                content,
            )
        elif action == "post":
            result = await adapter.write_post(
                title=context.title,
                body=content,
            )
        elif action == "upvote":
            result = await adapter.upvote(context.opportunity_id)
        else:
            return ActionRecord(
                opportunity_id=context.opportunity_id,
                action=action,
                platform=context.platform,
                url="",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ), {"error": f"지원하지 않는 action: {action}", "supported": ["comment", "post", "upvote"]}

    record = ActionRecord(
        opportunity_id=context.opportunity_id,
        action=action,
        platform=context.platform,
        url=result.url if result.success else "",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    response: dict[str, Any] = {
        "status": "posted" if result.success else "failed",
        "url": result.url,
        "platform": context.platform,
    }
    if not result.success:
        response["error"] = result.error

    return record, response


def _analyze_tone(comments: list[str]) -> str:
    """댓글 분위기를 간단 분석."""
    if not comments:
        return "neutral"

    positive_words = {"great", "awesome", "love", "thanks", "helpful", "cool", "nice", "좋", "감사"}
    negative_words = {"bad", "terrible", "hate", "spam", "wrong", "별로", "나쁨"}
    technical_words = {"api", "code", "implementation", "config", "setup", "deploy", "bug", "fix"}

    text = " ".join(comments).lower()
    pos = sum(1 for w in positive_words if w in text)
    neg = sum(1 for w in negative_words if w in text)
    tech = sum(1 for w in technical_words if w in text)

    if tech > 3:
        return "technical"
    if pos > neg * 2:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def _suggest_approach(opp: Opportunity, comment_count: int) -> str:
    """추천 접근 방식."""
    if comment_count > 20:
        return "comment"  # 활발한 토론에 참여
    if comment_count < 3:
        return "comment"  # 새 글에 초기 반응
    return "comment"


def _check_avoid(opp: Opportunity) -> list[str]:
    """주의사항 생성."""
    avoid: list[str] = []
    if opp.platform == "reddit":
        avoid.append("직접 링크 자제 — 서브레딧마다 셀프 홍보 규칙이 다름")
    if opp.platform == "twitter":
        avoid.append("280자 제한 — 핵심만 간결하게")
    avoid.append("과도한 홍보 톤 자제 — 자연스러운 대화체로")
    return avoid
