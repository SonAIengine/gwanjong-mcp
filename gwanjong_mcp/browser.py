"""Dev.to browser automation — Playwright persistent session."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from playwright.async_api import BrowserContext, async_playwright

logger = logging.getLogger(__name__)

BROWSER_DATA_DIR = Path(
    os.getenv("GWANJONG_BROWSER_DATA_DIR", str(Path.home() / ".gwanjong" / "browser-data"))
)
DEVTO_BASE = "https://dev.to"


async def _get_context() -> tuple:
    """Return a persistent browser context as a (playwright, context) tuple."""
    BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        str(BROWSER_DATA_DIR),
        headless=True,
        viewport={"width": 1280, "height": 720},
    )
    return pw, context


async def is_logged_in(context: BrowserContext) -> bool:
    """Check if logged in to Dev.to."""
    page = await context.new_page()
    try:
        await page.goto(DEVTO_BASE, wait_until="domcontentloaded", timeout=15000)
        # 로그인 상태면 프로필 아이콘이 있고, 미로그인이면 Log in 링크
        login_link = page.locator("a[href*='/enter']", has_text="Log in")
        return not await login_link.is_visible(timeout=3000)
    except Exception:
        logger.warning("Dev.to 로그인 상태 확인 실패", exc_info=True)
        return False
    finally:
        await page.close()


async def login_interactive() -> dict[str, str]:
    """Open browser for manual login. Switches to headful mode."""
    BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        str(BROWSER_DATA_DIR),
        headless=False,
        viewport={"width": 1280, "height": 720},
    )
    try:
        page = await context.new_page()
        await page.goto(f"{DEVTO_BASE}/enter", wait_until="domcontentloaded")

        # 로그인 완료 대기 (최대 120초)
        try:
            await page.wait_for_url(
                f"{DEVTO_BASE}/**",
                timeout=120000,
            )
            # 로그인 후 메인 페이지 리다이렉트 대기
            await page.wait_for_selector("[data-testid='navbar-user']", timeout=15000)
            return {"status": "ok", "message": "Dev.to login successful. Session saved."}
        except Exception:
            if "/enter" not in page.url:
                return {"status": "ok", "message": "Dev.to login likely successful. Session saved."}
            return {"status": "fail", "message": "Login timed out. Please try again."}
    finally:
        await context.close()
        await pw.stop()


async def devto_write_comment(article_id: str, article_url: str, body: str) -> dict[str, str]:
    """Write a comment on a Dev.to article via browser automation.

    Args:
        article_id: Dev.to article ID (numeric)
        article_url: Article URL
        body: Comment content (markdown)

    Returns:
        {"status": "ok"/"fail", "url": ..., "message": ...}
    """
    pw, context = await _get_context()
    try:
        # 로그인 확인
        if not await is_logged_in(context):
            await context.close()
            await pw.stop()
            # headful로 로그인 시도
            login_result = await login_interactive()
            if login_result["status"] != "ok":
                return {
                    "status": "fail",
                    "message": "Dev.to login required. " + login_result["message"],
                }
            # 재연결
            pw, context = await _get_context()

        page = await context.new_page()
        await page.goto(article_url, wait_until="domcontentloaded", timeout=20000)

        # 댓글 textarea: id="text-area", placeholder="Add to the discussion"
        textarea = page.locator("textarea#text-area")
        await textarea.wait_for(state="visible", timeout=10000)
        await textarea.click()
        await textarea.fill(body)

        # Submit 버튼
        submit_btn = page.locator("button.js-btn-enable", has_text="Submit")
        await submit_btn.click()

        # 댓글 등록 확인 — 페이지에 내 댓글이 나타날 때까지 대기
        await page.wait_for_timeout(3000)

        # 성공 여부 확인
        error_el = page.locator(".crayons-notice--danger, .error-message")
        if await error_el.is_visible(timeout=1000):
            error_text = await error_el.text_content()
            return {"status": "fail", "message": f"Comment post failed: {error_text}", "url": ""}

        return {
            "status": "ok",
            "message": "Comment posted successfully",
            "url": article_url + "#comments",
        }
    except Exception as e:
        logger.exception("Error writing Dev.to comment")
        return {"status": "fail", "message": str(e), "url": ""}
    finally:
        await context.close()
        await pw.stop()
