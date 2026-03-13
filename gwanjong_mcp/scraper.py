"""Twitter/X 비로그인 스크래핑 — Playwright headless.

API 호출 비용 절감을 위해 읽기 작업을 스크래핑으로 대체.
비로그인 상태로 접근 가능한 데이터만 처리:
  - get_tweet: 트윗 본문, 작성자, 시간, 메트릭 ✅
  - get_profile_tweets: 유저 프로필 최근 트윗 목록 ✅
  - get_comments: 비로그인 불가 ❌ → API fallback 필요
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from playwright.async_api import async_playwright, Page

logger = logging.getLogger(__name__)

_GOTO_TIMEOUT = 15000
_SELECTOR_TIMEOUT = 12000
_POST_LOAD_WAIT = 3000


@dataclass
class ScrapedTweet:
    """스크래핑된 트윗 데이터."""
    id: str
    author: str          # @handle
    display_name: str
    text: str
    url: str
    created_at: str      # ISO 8601
    likes: int
    retweets: int
    replies: int
    views: int


async def get_tweet(tweet_url: str) -> ScrapedTweet | None:
    """트윗 페이지 스크래핑. 비로그인 접근.

    Args:
        tweet_url: https://x.com/{user}/status/{id} 형태.
                   x.com/i/status/{id} 형태면 자동 변환.

    Returns:
        ScrapedTweet 또는 페이지 로딩 실패 시 None.
    """
    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        return await _scrape_tweet_page(page, tweet_url)
    except Exception:
        logger.exception("tweet 스크래핑 실패: %s", tweet_url)
        return None
    finally:
        if browser is not None:
            await browser.close()
        await pw.stop()


async def get_profile_tweets(username: str, limit: int = 10) -> list[ScrapedTweet]:
    """유저 프로필 페이지에서 최근 트윗 스크래핑.

    Args:
        username: @없이 handle만 (예: "sonseongjun97")
        limit: 최대 가져올 트윗 수

    Returns:
        ScrapedTweet 리스트 (최신순).
    """
    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        return await _scrape_profile_page(page, username, limit)
    except Exception:
        logger.exception("프로필 스크래핑 실패: %s", username)
        return []
    finally:
        if browser is not None:
            await browser.close()
        await pw.stop()


# ── 내부 구현 ──────────────────────────────────────────


async def _scrape_tweet_page(page: Page, tweet_url: str) -> ScrapedTweet | None:
    """트윗 상세 페이지 파싱."""
    await page.goto(tweet_url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)

    try:
        await page.wait_for_selector(
            'article[data-testid="tweet"]', timeout=_SELECTOR_TIMEOUT,
        )
    except Exception:
        logger.warning("article 로딩 실패: %s", tweet_url)
        return None

    raw = await page.eval_on_selector('article[data-testid="tweet"]', _JS_PARSE_TWEET)
    if not raw:
        return None

    tweet_id = _extract_tweet_id(tweet_url)
    return ScrapedTweet(
        id=tweet_id,
        author=raw.get("handle", "").lstrip("@"),
        display_name=raw.get("displayName", ""),
        text=raw.get("text", ""),
        url=tweet_url,
        created_at=raw.get("time", ""),
        likes=_parse_metric(raw.get("likeCount", "0")),
        retweets=_parse_metric(raw.get("retweetCount", "0")),
        replies=_parse_metric(raw.get("replyCount", "0")),
        views=_parse_metric(raw.get("viewCount", "0")),
    )


async def _scrape_profile_page(
    page: Page, username: str, limit: int,
) -> list[ScrapedTweet]:
    """프로필 페이지에서 트윗 목록 파싱."""
    url = f"https://x.com/{username}"
    await page.goto(url, wait_until="domcontentloaded", timeout=_GOTO_TIMEOUT)

    try:
        await page.wait_for_selector(
            'article[data-testid="tweet"]', timeout=_SELECTOR_TIMEOUT,
        )
    except Exception:
        logger.warning("프로필 트윗 로딩 실패: %s", username)
        return []

    # 스크롤해서 더 로드
    tweets: list[ScrapedTweet] = []
    seen_ids: set[str] = set()
    max_scrolls = 3

    for _ in range(max_scrolls):
        raw_list = await page.eval_on_selector_all(
            'article[data-testid="tweet"]', _JS_PARSE_TWEETS,
        )
        for raw in raw_list:
            tid = raw.get("tweetId", "")
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                tweets.append(ScrapedTweet(
                    id=tid,
                    author=raw.get("handle", "").lstrip("@"),
                    display_name=raw.get("displayName", ""),
                    text=raw.get("text", ""),
                    url=f"https://x.com/{username}/status/{tid}" if tid else "",
                    created_at=raw.get("time", ""),
                    likes=_parse_metric(raw.get("likeCount", "0")),
                    retweets=_parse_metric(raw.get("retweetCount", "0")),
                    replies=_parse_metric(raw.get("replyCount", "0")),
                    views=_parse_metric(raw.get("viewCount", "0")),
                ))
            if len(tweets) >= limit:
                return tweets

        if len(tweets) >= limit:
            break

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(_POST_LOAD_WAIT)

    return tweets[:limit]


# ── JavaScript 파싱 함수 ──────────────────────────────


_JS_PARSE_TWEET = """el => {
    const userName = el.querySelector('[data-testid="User-Name"]');
    const tweetText = el.querySelector('[data-testid="tweetText"]');
    const time = el.querySelector('time');

    let displayName = '', handle = '';
    if (userName) {
        const links = userName.querySelectorAll('a');
        for (const a of links) {
            const href = a.getAttribute('href') || '';
            if (href.startsWith('/') && !href.includes('/status/')) {
                const text = a.textContent || '';
                if (text.startsWith('@')) handle = text;
                else if (!displayName) displayName = text;
            }
        }
    }

    const metrics = {};
    for (const testId of ['reply', 'retweet', 'like']) {
        const btn = el.querySelector(`[data-testid="${testId}"]`);
        metrics[testId + 'Count'] = btn?.getAttribute('aria-label') || '0';
    }

    // views — aria-label에서 추출
    const viewLink = el.querySelector('a[href*="/analytics"]');
    metrics.viewCount = viewLink?.textContent || '0';

    return {
        displayName,
        handle,
        text: tweetText?.textContent || '',
        time: time?.getAttribute('datetime') || '',
        ...metrics,
    };
}"""


_JS_PARSE_TWEETS = """els => els.map(el => {
    const userName = el.querySelector('[data-testid="User-Name"]');
    const tweetText = el.querySelector('[data-testid="tweetText"]');
    const time = el.querySelector('time');

    let displayName = '', handle = '';
    if (userName) {
        const links = userName.querySelectorAll('a');
        for (const a of links) {
            const href = a.getAttribute('href') || '';
            if (href.startsWith('/') && !href.includes('/status/')) {
                const text = a.textContent || '';
                if (text.startsWith('@')) handle = text;
                else if (!displayName) displayName = text;
            }
        }
    }

    // tweet ID를 status 링크에서 추출
    let tweetId = '';
    const statusLink = el.querySelector('a[href*="/status/"]');
    if (statusLink) {
        const m = (statusLink.getAttribute('href') || '').match(/\\/status\\/(\\d+)/);
        if (m) tweetId = m[1];
    }

    const metrics = {};
    for (const testId of ['reply', 'retweet', 'like']) {
        const btn = el.querySelector(`[data-testid="${testId}"]`);
        metrics[testId + 'Count'] = btn?.getAttribute('aria-label') || '0';
    }

    const viewLink = el.querySelector('a[href*="/analytics"]');
    metrics.viewCount = viewLink?.textContent || '0';

    return {
        tweetId,
        displayName,
        handle,
        text: tweetText?.textContent || '',
        time: time?.getAttribute('datetime') || '',
        ...metrics,
    };
})"""


# ── 유틸리티 ──────────────────────────────────────────


def _extract_tweet_id(url: str) -> str:
    """URL에서 tweet ID 추출."""
    # x.com/user/status/123 또는 x.com/i/status/123
    parts = url.rstrip("/").split("/")
    for i, part in enumerate(parts):
        if part == "status" and i + 1 < len(parts):
            return parts[i + 1].split("?")[0]
    return ""


def _parse_metric(value: str) -> int:
    """메트릭 문자열을 정수로 변환.

    aria-label: '123 Likes' → 123
    텍스트: '1.2K' → 1200, '15' → 15
    """
    if not value:
        return 0
    # aria-label에서 숫자 추출: "123 replies" → "123"
    import re
    numbers = re.findall(r"[\d,]+\.?\d*[KkMm]?", value)
    if not numbers:
        return 0
    num_str = numbers[0].replace(",", "")
    if num_str.upper().endswith("K"):
        return int(float(num_str[:-1]) * 1000)
    if num_str.upper().endswith("M"):
        return int(float(num_str[:-1]) * 1_000_000)
    try:
        return int(float(num_str))
    except ValueError:
        return 0
