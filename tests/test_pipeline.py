"""pipeline 모듈 단위 테스트."""

from __future__ import annotations

from devhub.types import Post
from gwanjong_mcp.pipeline import _score_relevance, _analyze_tone, _generate_reason


def _make_post(**kwargs) -> Post:
    defaults = {
        "id": "1",
        "platform": "devto",
        "title": "Test Post",
        "url": "https://example.com",
        "body": "",
        "tags": [],
        "likes": 0,
        "comments_count": 0,
    }
    defaults.update(kwargs)
    return Post(**defaults)


def test_score_relevance_title_match():
    post = _make_post(title="Best MCP server for university")
    score = _score_relevance(post, "MCP server")
    assert score > 0.3


def test_score_relevance_no_match():
    post = _make_post(title="Cooking recipes")
    score = _score_relevance(post, "MCP server")
    assert score < 0.1


def test_score_relevance_tag_match():
    post = _make_post(title="My project", tags=["mcp", "python"])
    score = _score_relevance(post, "MCP")
    assert score >= 0.2


def test_score_capped_at_one():
    post = _make_post(
        title="MCP server MCP tool",
        body="MCP is great MCP everywhere",
        tags=["mcp", "server"],
        likes=100,
        comments_count=50,
    )
    score = _score_relevance(post, "MCP server")
    assert score <= 1.0


def test_analyze_tone_positive():
    comments = ["This is great!", "Awesome work, thanks for sharing"]
    assert _analyze_tone(comments) == "positive"


def test_analyze_tone_technical():
    comments = [
        "Check the API docs for config",
        "The implementation uses deploy pipeline with code fix",
    ]
    assert _analyze_tone(comments) == "technical"


def test_analyze_tone_empty():
    assert _analyze_tone([]) == "neutral"


def test_generate_reason():
    post = _make_post(comments_count=30, likes=60)
    reason = _generate_reason(post, "MCP", 0.8)
    assert "주제 직접 관련" in reason
    assert "활발한 토론" in reason
