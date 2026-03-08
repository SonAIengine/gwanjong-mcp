"""scout/draft/strike 파이프라인 로직."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from devhub import Hub
from devhub.registry import get_adapter_class
from devhub.types import Post

from .events import Event, EventBus
from .types import ActionRecord, DraftContext, Opportunity

logger = logging.getLogger(__name__)


def _score_relevance(post: Post, topic: str) -> float:
    """게시글의 주제 관련성 점수 (0.0 ~ 1.0). 플랫폼별 가중치 적용."""
    score = 0.0
    topic_lower = topic.lower()
    words = topic_lower.split()

    # 제목 매칭 (트윗 등 title 없는 플랫폼은 body로 보상)
    title_lower = post.title.lower()
    has_title = bool(title_lower.strip())
    for word in words:
        if has_title and word in title_lower:
            score += 0.3
        elif not has_title:
            pass  # body에서 보상

    # 본문 매칭 (title 없으면 가중치 상향)
    body_lower = post.body.lower()
    body_weight = 0.25 if not has_title else 0.1
    for word in words:
        if word in body_lower:
            score += body_weight

    # 태그 매칭
    for tag in post.tags:
        if tag.lower() in topic_lower or any(w in tag.lower() for w in words):
            score += 0.2

    # 플랫폼별 가중치 (주제 매칭이 있을 때만 적용)
    platform = post.platform
    if platform == "devto":
        # Dev.to: 태그 매칭 + 적당한 토론이 가치 높음
        if score > 0 and post.comments_count > 5:
            score += 0.15
        elif score > 0 and post.comments_count < 3:
            score += 0.1  # 초기 댓글 기회
    elif platform == "reddit":
        # Reddit: 댓글 깊이가 핵심 (활발한 토론 = 참여 가치)
        if score > 0 and post.comments_count > 20:
            score += 0.2
        elif score > 0 and post.comments_count > 5:
            score += 0.15
    elif platform == "twitter":
        # Twitter: 도달 범위 (likes) 가중
        if score > 0 and post.likes > 100:
            score += 0.2
        elif score > 0 and post.likes > 30:
            score += 0.1
    elif platform == "bluesky":
        # Bluesky: 대화형 — 적당한 활동이면 충분
        if score > 0 and post.comments_count > 3:
            score += 0.1
        if score > 0 and post.likes > 5:
            score += 0.1
    else:
        # 기본 가중치
        if score > 0 and post.comments_count > 20:
            score += 0.15
        elif score > 0 and post.comments_count > 5:
            score += 0.1

    # 인기도 보너스 (플랫폼 공통, Twitter는 위에서 처리)
    if platform != "twitter":
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
    bus: EventBus | None = None,
) -> tuple[dict[str, Opportunity], dict[str, Any]]:
    """정찰: trending + search → 점수화 → 상위 N개 기회 반환.

    Returns:
        (opportunities_dict, compressed_response)
    """
    hub = Hub.from_env()
    # 연결 전에 플랫폼 필터링 (불필요한 connect 방지)
    if platforms:
        hub.adapters = [a for a in hub.adapters if a.platform in platforms]

    async with hub:
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
            suggested_actions=_suggest_actions(
                # 임시 Opportunity로 actions 계산 (raw 필요)
                Opportunity(
                    id=opp_id, platform=post.platform, post_id=post.id,
                    title="", url="", relevance=0, comments_count=post.comments_count,
                    reason="", raw={"likes": post.likes},
                ),
                post.comments_count,
            ),
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
            "actions": opp.suggested_actions,
        })

    platforms_found = len({o.platform for o in opportunities.values()})
    response = {
        "opportunities": compressed,
        "total_scanned": len(all_posts),
        "summary": f"{platforms_found}개 플랫폼에서 {len(compressed)}건의 기회 발견",
    }

    if bus:
        await bus.emit(Event("scout.done", {
            "topic": topic,
            "count": len(opportunities),
            "opportunities": opportunities,
        }))

    return opportunities, response


async def draft(
    opportunity: Opportunity,
    bus: EventBus | None = None,
) -> tuple[DraftContext, dict[str, Any]]:
    """초안 준비: 게시글 + 댓글 트리 + 분위기 분석.

    Returns:
        (draft_context, compressed_response)
    """
    cls = get_adapter_class(opportunity.platform)

    adapter = cls()
    async with adapter:
        post = await adapter.get_post(opportunity.post_id)
        comments = await adapter.get_comments(opportunity.post_id, limit=20)

    # 분위기 분석 (간단 휴리스틱)
    comment_bodies = [c.body for c in comments]
    tone = _analyze_tone(comment_bodies)
    approach = _suggest_approach(opportunity, len(comments))
    avoid = _check_avoid(opportunity)
    writing_guide = _build_writing_guide(opportunity, tone)

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
        "writing_guide": writing_guide,
        "avoid": ctx.avoid,
    }

    if bus:
        await bus.emit(Event("draft.done", {
            "opportunity_id": opportunity.id,
            "platform": opportunity.platform,
            "context": ctx,
        }))

    return ctx, response


async def strike(
    context: DraftContext,
    action: str,
    content: str,
    bus: EventBus | None = None,
) -> tuple[ActionRecord, dict[str, Any]]:
    """실행: 댓글/게시글/교차게시 작성.

    Returns:
        (action_record, compressed_response)
    """
    # strike.before 이벤트 — safety 등 플러그인이 차단 가능
    if bus:
        await bus.emit(Event("strike.before", {
            "platform": context.platform,
            "action": action,
            "content": content,
            "context": context,
        }))

    cls = get_adapter_class(context.platform)

    # 플랫폼별 액션 검증 (adapter의 setup_guide에서 가져오되, 하드코딩 fallback)
    allowed = PLATFORM_ACTIONS.get(context.platform)
    if allowed is None:
        guide = cls.setup_guide()
        allowed = guide.get("allowed_actions", ["comment"])
    if action not in allowed:
        return ActionRecord(
            opportunity_id=context.opportunity_id,
            action=action,
            platform=context.platform,
            url="",
            timestamp=datetime.now(timezone.utc).isoformat(),
        ), {
            "error": f"{context.platform}에서 '{action}'은 허용되지 않음",
            "allowed_actions": allowed,
            "reason": "reddit은 self-promo 금지" if context.platform == "reddit" and action == "post" else "플랫폼 정책",
        }

    # Dev.to comment는 API 미지원 → 브라우저 자동화
    if context.platform == "devto" and action == "comment":
        from .browser import devto_write_comment
        # context.opportunity_id는 server.py에서 실제 post_id로 교체된 상태
        article_url = f"https://dev.to/api/articles/{context.opportunity_id}"
        # 실제 URL을 가져와서 사용
        adapter = cls()
        async with adapter:
            post = await adapter.get_post(context.opportunity_id)
        article_url = post.url

        browser_result = await devto_write_comment(
            article_id=context.opportunity_id,
            article_url=article_url,
            body=content,
        )
        record = ActionRecord(
            opportunity_id=context.opportunity_id,
            action=action,
            platform=context.platform,
            url=browser_result.get("url", ""),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        response: dict[str, Any] = {
            "status": "posted" if browser_result["status"] == "ok" else "failed",
            "url": browser_result.get("url", ""),
            "platform": context.platform,
        }
        if browser_result["status"] != "ok":
            response["error"] = browser_result["message"]
        if bus:
            await bus.emit(Event("strike.after", {
                "record": record,
                "response": response,
                "content": content,
            }))
        return record, response

    adapter = cls()
    async with adapter:
        if action == "comment":
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

    if bus:
        await bus.emit(Event("strike.after", {
            "record": record,
            "response": response,
            "content": content,
        }))

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


# 플랫폼별 허용 액션
PLATFORM_ACTIONS: dict[str, list[str]] = {
    "devto": ["comment", "post"],       # 댓글 기여 + 글 발행 (핵심 거점)
    "bluesky": ["comment", "post"],     # 리플 + 포스트 (네트워킹)
    "twitter": ["comment", "post"],     # 리플 (visibility) + 트윗 (홍보)
    "reddit": ["comment", "upvote"],    # 댓글만 (자기홍보 금지, post 차단)
}


def _suggest_approach(opp: Opportunity, comment_count: int) -> str:
    """플랫폼 특성 + 토론 상태에 따른 추천 접근 방식."""
    platform = opp.platform

    if platform == "devto":
        if comment_count < 3:
            return "comment"  # 새 글에 초기 반응 → 가시성 높음
        if comment_count > 15:
            return "comment"  # 활발한 토론 참여
        return "comment"  # Dev.to는 기본적으로 댓글 기여

    if platform == "bluesky":
        if comment_count > 5:
            return "comment"  # 대화에 참여
        return "post"  # 조용한 글이면 독립 포스트로 화제 만들기

    if platform == "twitter":
        if opp.raw.get("likes", 0) > 50:
            return "comment"  # 인기 트윗에 리플 → visibility 극대화
        return "post"  # 아니면 독립 트윗 (프로젝트 홍보)

    if platform == "reddit":
        return "comment"  # Reddit은 무조건 댓글. post는 downvote 폭격.

    return "comment"


def _suggest_actions(opp: Opportunity, comment_count: int) -> list[str]:
    """플랫폼별 추천 액션 목록 (우선순위 순)."""
    platform = opp.platform

    if platform == "devto":
        actions = ["comment"]
        if comment_count < 3:
            actions.append("post")  # 관련 주제로 글 발행도 고려
        return actions

    if platform == "bluesky":
        if comment_count > 5:
            return ["comment", "post"]  # 대화 참여 우선
        return ["post", "comment"]  # Build in Public 스타일

    if platform == "twitter":
        if opp.raw.get("likes", 0) > 50:
            return ["comment"]  # 인기 트윗에 리플만
        return ["post", "comment"]  # 홍보 트윗 우선

    if platform == "reddit":
        return ["comment", "upvote"]  # post 절대 금지

    return ["comment"]


def _check_avoid(opp: Opportunity) -> list[str]:
    """플랫폼별 주의사항 생성."""
    avoid: list[str] = []
    if opp.platform == "devto":
        avoid.append("Don't just drop a link to your project — add value first")
        avoid.append("Reference specific code/concepts from the post")
    elif opp.platform == "bluesky":
        avoid.append("Keep it conversational — Bluesky is not a blog")
        avoid.append("300 char limit per post — thread if needed")
    elif opp.platform == "twitter":
        avoid.append("280 char limit — one sharp point, not a summary")
        avoid.append("Don't reply-spam popular accounts")
    elif opp.platform == "reddit":
        avoid.append("NEVER post self-promo — instant downvote + possible ban")
        avoid.append("No direct links to your project unless asked")
        avoid.append("Read subreddit rules before commenting")
        avoid.append("Be blunt and helpful, not polished")
    avoid.extend(WRITING_AVOID)
    return avoid


# AI가 흔히 쓰는 패턴 — 반드시 피할 것
WRITING_AVOID = [
    "No generic praise openers ('This is amazing!', 'Great article!', 'Love this!')",
    "No 'Curious about...' or 'I'd love to hear...' — these scream AI",
    "No formulaic structure: compliment → personal experience → question",
    "No words: 'fascinating', 'insightful', 'resonates', 'game-changer', 'deep dive', 'kudos'",
    "No hedging ('I think', 'In my experience', 'It might be worth') — just say it",
    "Don't always end with a question — sometimes just make a statement",
    "No bullet points or numbered lists in comments — people don't do that",
]


def _build_writing_guide(opp: Opportunity, tone: str) -> str:
    """사람처럼 쓰기 위한 구체적 가이드."""
    guide = (
        "Write like a real developer leaving a comment, not an AI assistant.\n"
        "\n"
        "HOW REAL COMMENTS SOUND:\n"
        "- Short. 2-4 sentences max. Sometimes just one line.\n"
        "- Start mid-thought, like you're already in the conversation. "
        "('oh man, the knowledge graph part — ' not 'This is a really interesting approach to...')\n"
        "- Be specific. React to ONE concrete detail, not the whole post.\n"
        "- Typos and casual grammar are fine. Contractions always.\n"
        "- Share a quick personal detail if relevant ('I tried something similar with X and it broke in Y way')\n"
        "- Disagreement is OK. 'idk if that scales though' is more human than endless praise.\n"
        "- Match the energy of existing comments — if they're casual, be casual.\n"
    )

    if tone == "technical":
        guide += "- This thread is technical. Drop specific terms, reference concrete tradeoffs.\n"
    elif tone == "positive":
        guide += "- Thread is upbeat but don't pile on generic praise. Add substance.\n"

    if opp.platform == "devto":
        guide += "- Dev.to is casual-professional. Think 'smart coworker at lunch', not conference talk.\n"
    elif opp.platform == "reddit":
        guide += "- Reddit is blunt. No fluff. Get to the point or get downvoted.\n"
    elif opp.platform == "twitter":
        guide += "- Twitter is punchy. One sharp observation > a polished paragraph.\n"
    elif opp.platform == "bluesky":
        guide += "- Bluesky is conversational. Think quote-tweet energy.\n"

    return guide
