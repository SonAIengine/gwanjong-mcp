"""scraper.py tests — utility functions + Playwright integration."""

import pytest

from gwanjong_mcp.scraper import (
    ScrapedTweet,
    _extract_tweet_id,
    _parse_metric,
    get_tweet,
    get_profile_tweets,
)


async def _ensure_playwright_launchable() -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(headless=True)
    except Exception as exc:
        pytest.skip(f"Playwright browser launch unavailable in this environment: {exc}")
    finally:
        if browser is not None:
            await browser.close()
        await pw.stop()


# ── 유닛 테스트: _extract_tweet_id ──


def test_extract_tweet_id_standard():
    url = "https://x.com/user/status/2031198406534902031"
    assert _extract_tweet_id(url) == "2031198406534902031"


def test_extract_tweet_id_i_format():
    url = "https://x.com/i/status/2031198406534902031"
    assert _extract_tweet_id(url) == "2031198406534902031"


def test_extract_tweet_id_with_query():
    url = "https://x.com/user/status/123456?s=20"
    assert _extract_tweet_id(url) == "123456"


def test_extract_tweet_id_trailing_slash():
    url = "https://x.com/user/status/123456/"
    assert _extract_tweet_id(url) == "123456"


def test_extract_tweet_id_invalid():
    assert _extract_tweet_id("https://x.com/user") == ""
    assert _extract_tweet_id("") == ""


# ── 유닛 테스트: _parse_metric ──


def test_parse_metric_plain_number():
    assert _parse_metric("42") == 42
    assert _parse_metric("1234") == 1234


def test_parse_metric_with_commas():
    assert _parse_metric("1,234") == 1234
    assert _parse_metric("14,970") == 14970


def test_parse_metric_k_suffix():
    assert _parse_metric("1.2K") == 1200
    assert _parse_metric("5k") == 5000
    assert _parse_metric("14.9K") == 14900


def test_parse_metric_m_suffix():
    assert _parse_metric("1.5M") == 1500000
    assert _parse_metric("2M") == 2000000


def test_parse_metric_aria_label():
    """aria-label 형태: '123 replies', '5,088 Likes'"""
    assert _parse_metric("123 replies") == 123
    assert _parse_metric("5,088 Likes") == 5088
    assert _parse_metric("14.9K Likes") == 14900


def test_parse_metric_empty():
    assert _parse_metric("") == 0
    assert _parse_metric("0") == 0
    assert _parse_metric("no number here") == 0


# ── 통합 테스트: Playwright 스크래핑 (네트워크 필요) ──


@pytest.mark.integration
async def test_get_tweet_real():
    """실제 x.com 트윗 페이지 스크래핑."""
    await _ensure_playwright_launchable()
    # AnthropicAI의 고정 트윗 사용 (삭제될 가능성 낮음)
    tweet = await get_tweet("https://x.com/AnthropicAI/status/2029999833717838016")
    assert tweet is not None
    assert isinstance(tweet, ScrapedTweet)
    assert tweet.author == "AnthropicAI"
    assert tweet.id == "2029999833717838016"
    assert len(tweet.text) > 0
    assert tweet.created_at  # ISO 8601 문자열


@pytest.mark.integration
async def test_get_tweet_with_metrics():
    """메트릭(likes, retweets 등) 파싱 확인."""
    await _ensure_playwright_launchable()
    tweet = await get_tweet("https://x.com/AnthropicAI/status/2029999833717838016")
    assert tweet is not None
    # AnthropicAI 트윗은 보통 likes가 있음
    assert tweet.likes >= 0
    assert tweet.retweets >= 0
    assert tweet.replies >= 0


@pytest.mark.integration
async def test_get_tweet_invalid_url():
    """존재하지 않는 트윗 → None."""
    await _ensure_playwright_launchable()
    tweet = await get_tweet("https://x.com/nobody/status/9999999999999999999")
    assert tweet is None


@pytest.mark.integration
async def test_get_profile_tweets_real():
    """실제 프로필 페이지 스크래핑."""
    await _ensure_playwright_launchable()
    tweets = await get_profile_tweets("AnthropicAI", limit=3)
    assert len(tweets) > 0
    assert len(tweets) <= 3
    for t in tweets:
        assert isinstance(t, ScrapedTweet)
        assert t.author == "AnthropicAI"
        assert t.id
        assert t.text


@pytest.mark.integration
async def test_get_profile_tweets_empty_user():
    """트윗 없는 유저 → 빈 리스트."""
    await _ensure_playwright_launchable()
    tweets = await get_profile_tweets("thisuserdoesnotexist99999", limit=3)
    assert tweets == []
