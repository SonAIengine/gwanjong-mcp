"""Platform onboarding — status check, guidance, key storage, connection test."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from devhub.registry import get_adapter_class, get_adapter_classes

ENV_PATH = Path.home() / ".gwanjong" / ".env"


def _get_guides() -> dict[str, dict[str, Any]]:
    """Dynamically collect setup_guide from all registered adapters in the registry."""
    return {name: cls.setup_guide() for name, cls in get_adapter_classes().items()}


def _load_env() -> dict[str, str]:
    """Load existing .env file into a dict."""
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _save_env(env: dict[str, str]) -> None:
    """Save a dict to the .env file."""
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for key, value in env.items():
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def check_platforms() -> dict[str, Any]:
    """Check configuration status of each platform (dynamically detected from registry)."""
    env = _load_env()
    merged = {**os.environ, **env}

    configured: list[str] = []
    not_configured: list[str] = []

    for platform, guide in _get_guides().items():
        if _has_required_config(guide, merged):
            configured.append(platform)
        else:
            not_configured.append(platform)

    return {"configured": configured, "not_configured": not_configured}


def get_guide(platform: str) -> dict[str, Any]:
    """Return API key setup instructions for a specific platform."""
    guides = _get_guides()
    if platform not in guides:
        return {"error": f"지원하지 않는 플랫폼: {platform}", "supported": list(guides)}
    guide = guides[platform]
    return {
        "platform": platform,
        "url": guide.get("url", ""),
        "steps": guide.get("steps", []),
        "required_keys": guide.get("required_keys", []),
        "required_any": guide.get("required_any", []),
        "optional_keys": guide.get("optional_keys", []),
        "allowed_actions": guide.get("allowed_actions", ["comment"]),
    }


def save_credentials(platform: str, credentials: dict[str, str]) -> dict[str, Any]:
    """Save API keys to .env."""
    guides = _get_guides()
    if platform not in guides:
        return {"error": f"지원하지 않는 플랫폼: {platform}", "supported": list(guides)}

    guide = guides[platform]
    required = guide.get("required_keys", [])
    env = _load_env()
    merged = {**env, **credentials}
    missing = [k for k in required if not merged.get(k)]
    if missing:
        return {"error": f"누락된 키: {', '.join(missing)}", "required_keys": required}
    required_any = guide.get("required_any", [])
    missing_any = [group for group in required_any if not any(merged.get(key) for key in group)]
    if missing_any:
        return {
            "error": "대체 필수 키가 누락됨",
            "required_any": required_any,
        }
    validation_error = _validate_credentials(platform, merged)
    if validation_error:
        return validation_error

    # 기존 .env 로드 후 upsert
    for key, value in credentials.items():
        env[key] = value
        os.environ[key] = value  # 현재 프로세스에도 반영
    _save_env(env)

    return {"saved": True, "platform": platform, "path": str(ENV_PATH)}


async def test_connection(platform: str) -> dict[str, Any]:
    """Test platform connection (get_trending limit=1)."""
    try:
        cls = get_adapter_class(platform)
    except KeyError:
        return {"test": "fail", "message": f"지원하지 않는 플랫폼: {platform}"}

    if not cls.is_configured():
        return {"test": "fail", "message": f"{platform} 환경변수가 설정되지 않음"}

    try:
        adapter = cls()
        async with adapter:
            posts = await adapter.get_trending(limit=1)
            return {
                "test": "ok",
                "message": f"{platform} 연결 성공. {len(posts)}개 포스트 조회 확인.",
            }
    except Exception as e:
        return {"test": "fail", "message": f"{platform} 연결 실패: {e}"}


def _has_required_config(guide: dict[str, Any], values: dict[str, str]) -> bool:
    required = guide.get("required_keys", [])
    if any(not values.get(key) for key in required):
        return False
    required_any = guide.get("required_any", [])
    return all(any(values.get(key) for key in group) for group in required_any)


def _validate_credentials(platform: str, values: dict[str, str]) -> dict[str, Any] | None:
    if platform == "github_discussions":
        repos = {
            part.strip()
            for part in values.get("GITHUB_DISCUSSIONS_REPOS", "").split(",")
            if part.strip()
        }
        default_repo = values.get("GITHUB_DISCUSSIONS_DEFAULT_REPO", "").strip()
        category_id = values.get("GITHUB_DISCUSSIONS_CATEGORY_ID", "").strip()
        if default_repo and default_repo not in repos:
            return {
                "error": "GITHUB_DISCUSSIONS_DEFAULT_REPO must be included in GITHUB_DISCUSSIONS_REPOS"
            }
        if category_id and not default_repo:
            return {
                "error": "GITHUB_DISCUSSIONS_CATEGORY_ID requires GITHUB_DISCUSSIONS_DEFAULT_REPO"
            }

    if platform == "discourse":
        base_urls = {
            values.get("DISCOURSE_BASE_URL", "").strip(),
            *[
                part.strip()
                for part in values.get("DISCOURSE_BASE_URLS", "").split(",")
                if part.strip()
            ],
        }
        base_urls.discard("")
        default_base = values.get("DISCOURSE_DEFAULT_BASE_URL", "").strip()
        if default_base and default_base not in base_urls:
            return {
                "error": "DISCOURSE_DEFAULT_BASE_URL must match DISCOURSE_BASE_URL or be included in DISCOURSE_BASE_URLS"
            }

    return None
