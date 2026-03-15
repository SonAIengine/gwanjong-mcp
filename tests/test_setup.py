"""setup 모듈 단위 테스트."""

from __future__ import annotations

from pathlib import Path

from gwanjong_mcp import setup
from gwanjong_mcp.setup import _get_guides, check_platforms, get_guide, save_credentials


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
        assert "optional_keys" in guide
        assert len(guide["steps"]) > 0
        assert len(guide["required_keys"]) > 0


def test_get_guide_invalid():
    result = get_guide("invalid_platform")
    assert "error" in result


def test_all_platforms_have_guides():
    """기본 내장 플랫폼은 모두 가이드가 있어야 함."""
    guides = _get_guides()
    assert "devto" in guides
    assert "bluesky" in guides
    assert "twitter" in guides
    assert "reddit" in guides
    assert "github_discussions" in guides
    assert "discourse" in guides


def test_save_credentials_supports_optional_github_fields(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(setup, "ENV_PATH", tmp_path / ".env")
    result = save_credentials(
        "github_discussions",
        {
            "GITHUB_TOKEN": "token",
            "GITHUB_DISCUSSIONS_REPOS": "openai/openai-python",
            "GITHUB_DISCUSSIONS_DEFAULT_REPO": "openai/openai-python",
            "GITHUB_DISCUSSIONS_CATEGORY_ID": "DIC_kwDO",
        },
    )
    assert result["saved"] is True
    saved = (tmp_path / ".env").read_text()
    assert "GITHUB_DISCUSSIONS_DEFAULT_REPO=openai/openai-python" in saved
    assert "GITHUB_DISCUSSIONS_CATEGORY_ID=DIC_kwDO" in saved


def test_save_credentials_accepts_discourse_base_urls(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(setup, "ENV_PATH", tmp_path / ".env")
    result = save_credentials(
        "discourse",
        {
            "DISCOURSE_BASE_URLS": "https://forum-a.example.com,https://forum-b.example.com",
            "DISCOURSE_API_KEY": "key",
            "DISCOURSE_API_USERNAME": "alice",
            "DISCOURSE_DEFAULT_BASE_URL": "https://forum-a.example.com",
        },
    )
    assert result["saved"] is True
    saved = (tmp_path / ".env").read_text()
    assert "DISCOURSE_BASE_URLS=https://forum-a.example.com,https://forum-b.example.com" in saved
    assert "DISCOURSE_DEFAULT_BASE_URL=https://forum-a.example.com" in saved


def test_save_credentials_rejects_github_category_without_default_repo(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(setup, "ENV_PATH", tmp_path / ".env")
    result = save_credentials(
        "github_discussions",
        {
            "GITHUB_TOKEN": "token",
            "GITHUB_DISCUSSIONS_REPOS": "openai/openai-python",
            "GITHUB_DISCUSSIONS_CATEGORY_ID": "DIC_kwDO",
        },
    )
    assert "requires GITHUB_DISCUSSIONS_DEFAULT_REPO" in result["error"]


def test_save_credentials_rejects_discourse_unknown_default_base(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(setup, "ENV_PATH", tmp_path / ".env")
    result = save_credentials(
        "discourse",
        {
            "DISCOURSE_BASE_URLS": "https://forum-a.example.com",
            "DISCOURSE_DEFAULT_BASE_URL": "https://forum-b.example.com",
            "DISCOURSE_API_KEY": "key",
            "DISCOURSE_API_USERNAME": "alice",
        },
    )
    assert "DISCOURSE_DEFAULT_BASE_URL must match" in result["error"]
