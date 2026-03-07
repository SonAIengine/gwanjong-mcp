"""setup 모듈 단위 테스트."""

from __future__ import annotations

from gwanjong_mcp.setup import check_platforms, get_guide, PLATFORM_GUIDES


def test_check_platforms():
    result = check_platforms()
    assert "configured" in result
    assert "not_configured" in result
    # configured + not_configured = 전체 플랫폼
    total = len(result["configured"]) + len(result["not_configured"])
    assert total == len(PLATFORM_GUIDES)


def test_get_guide_valid():
    for platform in PLATFORM_GUIDES:
        guide = get_guide(platform)
        assert guide["platform"] == platform
        assert "steps" in guide
        assert "required_keys" in guide
        assert len(guide["steps"]) > 0
        assert len(guide["required_keys"]) > 0


def test_get_guide_invalid():
    result = get_guide("invalid_platform")
    assert "error" in result


def test_all_platforms_have_guides():
    """4개 플랫폼 모두 가이드가 있어야 함."""
    assert "devto" in PLATFORM_GUIDES
    assert "bluesky" in PLATFORM_GUIDES
    assert "twitter" in PLATFORM_GUIDES
    assert "reddit" in PLATFORM_GUIDES
