"""setup 모듈 단위 테스트."""

from __future__ import annotations

from gwanjong_mcp.setup import _get_guides, check_platforms, get_guide


def test_check_platforms():
    guides = _get_guides()
    result = check_platforms()
    assert "configured" in result
    assert "not_configured" in result
    # configured + not_configured = 전체 플랫폼
    total = len(result["configured"]) + len(result["not_configured"])
    assert total == len(guides)


def test_get_guide_valid():
    guides = _get_guides()
    for platform in guides:
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
    guides = _get_guides()
    assert "devto" in guides
    assert "bluesky" in guides
    assert "twitter" in guides
    assert "reddit" in guides
