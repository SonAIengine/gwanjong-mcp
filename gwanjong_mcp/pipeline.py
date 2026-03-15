"""Scout/draft/strike pipeline logic."""

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


_SPAM_KEYWORDS = {
    "whale",
    "token",
    "$",
    "sol",
    "solana",
    "nft",
    "airdrop",
    "pump",
    "moon",
    "degen",
    "buy",
    "sell",
    "market cap",
    "ca:",
    "txs",
    "mcap",
    "🐳",
    "💎",
    "🚀",
}


def _is_spam(post: Post) -> bool:
    """Filter out crypto/spam tweets."""
    text = f"{post.title} {post.body}".lower()
    hits = sum(1 for kw in _SPAM_KEYWORDS if kw in text)
    return hits >= 3


def _is_reply_restricted(post: Post) -> bool:
    """Filter reply-restricted tweets on Twitter. Always returns False for other platforms."""
    if post.platform != "twitter":
        return False
    return post.raw.get("reply_settings", "everyone") != "everyone"


def _score_relevance(post: Post, topic: str) -> float:
    """Score topic relevance of a post (0.0 to 1.0). Applies platform-specific weights."""
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
    elif platform == "github_discussions":
        # GitHub Discussions: repo 문맥 + 기술 토론이 핵심
        if score > 0 and post.comments_count > 8:
            score += 0.15
        elif score > 0 and post.comments_count > 2:
            score += 0.1
        if score > 0 and post.likes > 5:
            score += 0.1
    elif platform == "discourse":
        # Discourse: 검색 가능한 포럼 답변, 중간 이상 길이의 토론이 유리
        if score > 0 and post.comments_count > 10:
            score += 0.15
        elif score > 0 and post.comments_count > 3:
            score += 0.1
        if score > 0 and post.likes > 10:
            score += 0.05
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
    """Generate the reason for selecting an opportunity."""
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
    campaign_id: str = "",
) -> tuple[dict[str, Opportunity], dict[str, Any]]:
    """Scout: trending + search -> score -> return top N opportunities.

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
        platform_errors: dict[str, dict[str, Any]] = {}
        for operation in ("get_trending", "search"):
            for platform, error in hub.last_errors.get(operation, {}).items():
                platform_errors.setdefault(platform, {})[operation] = error

    # 중복 제거 (URL 기준) + 스팸 필터
    seen: set[str] = set()
    all_posts: list[Post] = []
    for post in trending + searched:
        if post.url not in seen and not _is_spam(post) and not _is_reply_restricted(post):
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
            title=(post.title or post.body or "")[:80] or "(no title)",
            url=post.url,
            relevance=round(score, 2),
            comments_count=post.comments_count,
            reason=_generate_reason(post, topic, score),
            suggested_actions=_suggest_actions(
                # 임시 Opportunity로 actions 계산 (raw 필요)
                Opportunity(
                    id=opp_id,
                    platform=post.platform,
                    post_id=post.id,
                    title="",
                    url="",
                    relevance=0,
                    comments_count=post.comments_count,
                    reason="",
                    raw={"likes": post.likes},
                ),
                post.comments_count,
            ),
            raw={"author": post.author, "likes": post.likes, "tags": post.tags},
        )
        opportunities[opp_id] = opp
        compressed.append(
            {
                "id": opp_id,
                "platform": opp.platform,
                "title": opp.title,
                "relevance": opp.relevance,
                "comments": opp.comments_count,
                "reason": opp.reason,
                "actions": opp.suggested_actions,
            }
        )

    platforms_found = len({o.platform for o in opportunities.values()})
    response = {
        "opportunities": compressed,
        "total_scanned": len(all_posts),
        "summary": f"{platforms_found}개 플랫폼에서 {len(compressed)}건의 기회 발견",
    }
    if platform_errors:
        degraded = sorted(platform_errors)
        response["degraded_platforms"] = degraded
        response["platform_errors"] = platform_errors
        response["summary"] = f"{response['summary']} ({len(degraded)}개 플랫폼 일부 요청 실패)"

    if bus:
        event_data: dict[str, Any] = {
            "topic": topic,
            "count": len(opportunities),
            "opportunities": opportunities,
            "response": response,
        }
        if campaign_id:
            event_data["campaign_id"] = campaign_id
        await bus.emit(Event("scout.done", event_data))

    return opportunities, response


async def draft(
    opportunity: Opportunity,
    bus: EventBus | None = None,
) -> tuple[DraftContext, dict[str, Any]]:
    """Prepare draft: post + comment tree + tone analysis.

    Twitter replaces get_post with Playwright scraping (saves API costs).
    Comments cannot be scraped without login, so API is still used.

    Returns:
        (draft_context, compressed_response)
    """
    cls = get_adapter_class(opportunity.platform)

    if opportunity.platform == "twitter" and opportunity.url:
        # Twitter: get_post를 스크래핑으로 대체 (API 호출 1건 절감)
        post, comments = await _draft_twitter(opportunity, cls)
    else:
        adapter = cls()
        async with adapter:
            post = await adapter.get_post(opportunity.post_id)
            comments = await adapter.get_comments(opportunity.post_id, limit=20)

    # 분위기 분석 (간단 휴리스틱)
    comment_bodies = [c.body for c in comments]
    tone = _analyze_tone(comment_bodies)
    approach = _suggest_approach(opportunity, len(comments))
    avoid = _check_avoid(opportunity)
    writing_guide = _build_writing_guide(opportunity, tone, action=approach)

    ctx = DraftContext(
        opportunity_id=opportunity.id,
        platform=opportunity.platform,
        title=post.title,
        body_summary=post.body[:500] if post.body else "",
        post_id=opportunity.post_id,
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
        await bus.emit(
            Event(
                "draft.done",
                {
                    "opportunity_id": opportunity.id,
                    "platform": opportunity.platform,
                    "context": ctx,
                },
            )
        )

    return ctx, response


async def _draft_twitter(
    opportunity: Opportunity,
    adapter_cls: type,
) -> tuple[Post, list[Any]]:
    """Twitter draft: get_post via scraping, get_comments via API.

    Saves one get_post API call through unauthenticated scraping.
    Comments require authentication, so API fallback is used.
    """
    from .scraper import get_tweet

    scraped = await get_tweet(opportunity.url)

    if scraped:
        post = Post(
            id=scraped.id,
            platform="twitter",
            title=scraped.text[:80] if scraped.text else "",
            url=scraped.url,
            body=scraped.text,
            author=scraped.author,
            likes=scraped.likes,
            comments_count=scraped.replies,
            raw={"scraped": True},
        )
    else:
        # 스크래핑 실패 시 API fallback
        logger.warning("Twitter 스크래핑 실패, API fallback: %s", opportunity.url)
        adapter = adapter_cls()
        async with adapter:
            post = await adapter.get_post(opportunity.post_id)

    # 댓글은 API로만 가능 (비로그인 스크래핑 불가)
    adapter = adapter_cls()
    async with adapter:
        comments = await adapter.get_comments(opportunity.post_id, limit=20)

    return post, comments


async def strike(
    context: DraftContext,
    action: str,
    content: str,
    bus: EventBus | None = None,
    campaign_id: str = "",
) -> tuple[ActionRecord, dict[str, Any]]:
    """Execute: write comment/post/crosspost.

    Returns:
        (action_record, compressed_response)
    """
    target_post_id = context.post_id or context.opportunity_id

    # strike.before 이벤트 — safety 등 플러그인이 차단 가능
    if bus:
        before_data: dict[str, Any] = {
            "platform": context.platform,
            "action": action,
            "content": content,
            "context": context,
        }
        if campaign_id:
            before_data["campaign_id"] = campaign_id
        await bus.emit(Event("strike.before", before_data))

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
            post_id=target_post_id,
        ), {
            "error": f"{context.platform}에서 '{action}'은 허용되지 않음",
            "allowed_actions": allowed,
            "reason": "reddit은 self-promo 금지"
            if context.platform == "reddit" and action == "post"
            else "플랫폼 정책",
        }

    # Dev.to comment는 API 미지원 → 브라우저 자동화
    if context.platform == "devto" and action == "comment":
        from .browser import devto_write_comment

        article_url = f"https://dev.to/api/articles/{target_post_id}"
        # 실제 URL을 가져와서 사용
        adapter = cls()
        async with adapter:
            post = await adapter.get_post(target_post_id)
        article_url = post.url

        browser_result = await devto_write_comment(
            article_id=target_post_id,
            article_url=article_url,
            body=content,
        )
        record = ActionRecord(
            opportunity_id=context.opportunity_id,
            action=action,
            platform=context.platform,
            url=browser_result.get("url", ""),
            timestamp=datetime.now(timezone.utc).isoformat(),
            post_id=target_post_id,
        )
        response: dict[str, Any] = {
            "status": "posted" if browser_result["status"] == "ok" else "failed",
            "url": browser_result.get("url", ""),
            "platform": context.platform,
        }
        if browser_result["status"] != "ok":
            response["error"] = browser_result["message"]
        if bus:
            after_data: dict[str, Any] = {
                "record": record,
                "response": response,
                "content": content,
            }
            if campaign_id:
                after_data["campaign_id"] = campaign_id
            await bus.emit(Event("strike.after", after_data))
        return record, response

    adapter = cls()
    async with adapter:
        if action == "comment":
            result = await adapter.write_comment(
                target_post_id,
                content,
            )
        elif action == "post":
            result = await adapter.write_post(
                title=context.title,
                body=content,
            )
        elif action == "upvote":
            result = await adapter.upvote(target_post_id)
        else:
            return ActionRecord(
                opportunity_id=context.opportunity_id,
                action=action,
                platform=context.platform,
                url="",
                timestamp=datetime.now(timezone.utc).isoformat(),
                post_id=target_post_id,
            ), {
                "error": f"지원하지 않는 action: {action}",
                "supported": ["comment", "post", "upvote"],
            }

    record = ActionRecord(
        opportunity_id=context.opportunity_id,
        action=action,
        platform=context.platform,
        url=result.url if result.success else "",
        timestamp=datetime.now(timezone.utc).isoformat(),
        post_id=target_post_id,
    )

    response: dict[str, Any] = {
        "status": "posted" if result.success else "failed",
        "url": result.url,
        "platform": context.platform,
    }
    if not result.success:
        response["error"] = result.error

    if bus:
        after_data2: dict[str, Any] = {
            "record": record,
            "response": response,
            "content": content,
        }
        if campaign_id:
            after_data2["campaign_id"] = campaign_id
        await bus.emit(Event("strike.after", after_data2))

    return record, response


def _analyze_tone(comments: list[str]) -> str:
    """Simple tone analysis of comments."""
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
    "devto": ["comment", "post"],  # 댓글 기여 + 글 발행 (핵심 거점)
    "bluesky": ["comment", "post"],  # 리플 + 포스트 (네트워킹)
    "twitter": ["comment", "post"],  # 리플 (visibility) + 트윗 (홍보)
    "reddit": ["comment", "upvote"],  # 댓글만 (자기홍보 금지, post 차단)
    "github_discussions": ["comment", "post", "upvote"],  # OSS/repo discussion 참여
    "discourse": ["comment", "post", "upvote"],  # 포럼 답변 + 독립 topic 작성
}


def _suggest_approach(opp: Opportunity, comment_count: int) -> str:
    """Suggest approach based on platform characteristics and discussion state."""
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

    if platform == "github_discussions":
        if comment_count > 0:
            return "comment"  # 기존 논의에 구체적으로 붙는 편이 안전
        return "post"  # 논의가 비어 있으면 새 discussion도 가능

    if platform == "discourse":
        if comment_count > 8:
            return "comment"  # 이미 흐름이 있으면 답변 참여
        if comment_count < 2:
            return "post"  # 조용한 포럼이면 독립 topic 가치도 있음
        return "comment"

    return "comment"


def _suggest_actions(opp: Opportunity, comment_count: int) -> list[str]:
    """Suggested action list per platform (in priority order)."""
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

    if platform == "github_discussions":
        if comment_count > 0:
            return ["comment", "upvote"]
        return ["post", "comment"]

    if platform == "discourse":
        if comment_count > 8:
            return ["comment", "upvote"]
        return ["comment", "post"]

    return ["comment"]


def _check_avoid(opp: Opportunity) -> list[str]:
    """Generate platform-specific cautions."""
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
    elif opp.platform == "github_discussions":
        avoid.append("Don't drop generic product promo into a repo discussion")
        avoid.append("Reference the exact repo context, API surface, or maintainer concern")
        avoid.append("If you're unsure, ask a concrete technical question instead of pitching")
    elif opp.platform == "discourse":
        avoid.append("Read the category norms before posting a new topic")
        avoid.append("Don't cross-post the same pitch across multiple forum topics")
        avoid.append("Prefer a direct answer with evidence over polished marketing language")
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


def _build_writing_guide(opp: Opportunity, tone: str, action: str = "comment") -> str:
    """Concrete writing guide for human-like tone. Branches into comment/post based on action."""
    if action == "post":
        return _build_post_guide(opp)
    return _build_comment_guide(opp, tone)


def _build_comment_guide(opp: Opportunity, tone: str) -> str:
    """Writing guide for comments."""
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
    elif opp.platform == "github_discussions":
        guide += (
            "- GitHub Discussions is repo-context heavy. Mention exact APIs, files, or tradeoffs.\n"
        )
    elif opp.platform == "discourse":
        guide += "- Discourse rewards clear answers. A concrete fix beats a clever opener.\n"

    return guide


def _build_post_guide(opp: Opportunity) -> str:
    """Writing guide for original posts/tweets."""
    guide = (
        "You're chatting with fellow devs about something you built. Like a conversation, not a pitch.\n"
        "\n"
        "TONE & STRUCTURE:\n"
        "- Conversational storytelling. Talk TO people, not AT them.\n"
        "  Good: 'LLM agent에 tool을 많이 넣으면 늘 이런 문제가 생기죠.'\n"
        "  Bad: 'We present a novel approach to tool retrieval.'\n"
        "- Start with a relatable problem. Make the reader nod along.\n"
        "- Walk through the solution like explaining to a friend over coffee.\n"
        "- Use rhetorical questions to pull readers in: '이거 어떻게 해결하냐고요?'\n"
        "- Numbers/benchmarks woven into the story, not listed.\n"
        "  Good: '248개 tool 던져봤더니 정확도가 20%로 떨어지더라고요 😅'\n"
        "  Bad: 'Accuracy: 20% (baseline), 72% (ours)'\n"
        "- Show genuine excitement but keep it grounded.\n"
        "- Emoji for warmth and rhythm — like you'd use in a group chat.\n"
        "- Be honest about rough edges. Trust comes from honesty.\n"
        "- End with a soft invitation: 'feedback welcome' or 'curious what you think'\n"
        "- Feature breakdown: explain WHAT each feature does and WHY it matters.\n"
        "  Don't just list features — connect each one to a real pain point.\n"
    )

    if opp.platform == "twitter":
        guide += (
            "\nTWITTER THREAD FORMAT:\n"
            "- Use a thread (4-7 tweets). Project intros need room to breathe.\n"
            "- Tweet 1: 🔥 Hook — the problem everyone relates to + emoji to set the mood.\n"
            "- Tweet 2-3: The solution, told as a story. 'so I ended up building...'\n"
            "- Tweet 3-5: Feature walkthrough. One feature per tweet, explain why it matters.\n"
            "  Each feature tweet: what it does → why you'd care → maybe a quick example.\n"
            "- Tweet 6: Concrete result — benchmark, before/after, real example.\n"
            "- Last tweet: Link + where to find it + soft CTA.\n"
            "- Each tweet ≤ 280 chars. Should feel like a series of DMs, not an essay.\n"
            "- Hashtags: 1-2 on the last tweet only.\n"
            "- Korean threads: 구어체 OK. '~죠', '~거예요', '~거든요' 자연스럽게.\n"
            "- English threads: casual, contractions, lowercase energy.\n"
        )
    elif opp.platform == "bluesky":
        guide += (
            "\nBLUESKY STYLE:\n"
            "- 300 chars per post. Thread if needed.\n"
            "- 'build in public' energy — share the journey, not just the product.\n"
            "- Self-deprecation works. The community values realness.\n"
        )
    elif opp.platform == "devto":
        guide += (
            "\nDEV.TO STYLE:\n"
            "- Story format: 'I had this problem → tried X → built Y'\n"
            "- Code blocks expected. Quick-start that actually runs.\n"
            "- Teaching a friend, not writing documentation.\n"
            "- Tags: 3-4 relevant ones for discovery.\n"
        )
    elif opp.platform == "github_discussions":
        guide += (
            "\nGITHUB DISCUSSIONS STYLE:\n"
            "- Only start a new discussion when it is repo-specific and actionable.\n"
            "- Put the concrete problem in the title, not vague thought leadership.\n"
            "- Include repro context, expected behavior, and why this matters to maintainers/users.\n"
            "- Keep the tone collaborative. You're contributing to the project's shared backlog.\n"
        )
    elif opp.platform == "discourse":
        guide += (
            "\nDISCOURSE STYLE:\n"
            "- Use a searchable title and front-load the actual problem.\n"
            "- A short summary plus numbered repro steps is acceptable here.\n"
            "- Link out only when the forum post still stands on its own.\n"
            "- Forums remember spam. Optimize for usefulness, not visibility.\n"
        )

    return guide
