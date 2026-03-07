"""플랫폼 온보딩 — 상태 확인, 안내, 키 저장, 연결 테스트."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ENV_PATH = Path.home() / ".gwanjong" / ".env"

PLATFORM_GUIDES: dict[str, dict[str, Any]] = {
    "devto": {
        "url": "https://dev.to/settings/extensions",
        "steps": [
            "1. https://dev.to/settings/extensions 접속",
            "2. 'DEV API Keys' 섹션에서 description 입력",
            "3. 'Generate API Key' 클릭",
            "4. 생성된 API Key 복사",
        ],
        "required_keys": ["DEVTO_API_KEY"],
    },
    "bluesky": {
        "url": "https://bsky.app/settings",
        "steps": [
            "1. https://bsky.app/settings 접속",
            "2. 'App Passwords' 클릭",
            "3. 앱 이름 입력 (예: gwanjong) 후 생성",
            "4. handle (예: user.bsky.social)과 생성된 앱 비밀번호 복사",
        ],
        "required_keys": ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
    },
    "twitter": {
        "url": "https://developer.x.com/en/portal/dashboard",
        "steps": [
            "1. https://developer.x.com/en/portal/dashboard 접속",
            "2. 프로젝트/앱 생성 (Free tier 가능)",
            "3. 'Keys and Tokens' 탭에서 API Key, API Secret 복사",
            "4. 'Authentication Tokens'에서 Access Token, Access Secret 생성 후 복사",
        ],
        "required_keys": [
            "TWITTER_API_KEY",
            "TWITTER_API_SECRET",
            "TWITTER_ACCESS_TOKEN",
            "TWITTER_ACCESS_SECRET",
        ],
    },
    "reddit": {
        "url": "https://www.reddit.com/prefs/apps",
        "steps": [
            "1. https://www.reddit.com/prefs/apps 접속",
            "2. 'create another app' 클릭",
            "3. name: gwanjong, type: script 선택",
            "4. redirect uri: http://localhost:8080 입력",
            "5. 생성 후 client_id (앱 이름 아래 문자열)와 secret 복사",
            "6. Reddit 계정의 username, password도 필요",
        ],
        "required_keys": [
            "REDDIT_CLIENT_ID",
            "REDDIT_CLIENT_SECRET",
            "REDDIT_USERNAME",
            "REDDIT_PASSWORD",
        ],
    },
}


def _load_env() -> dict[str, str]:
    """기존 .env 파일을 dict로 로드."""
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
    """dict를 .env 파일로 저장."""
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for key, value in env.items():
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def check_platforms() -> dict[str, Any]:
    """각 플랫폼의 설정 상태를 확인."""
    env = _load_env()
    # 환경변수도 함께 확인
    merged = {**env}
    for key in os.environ:
        if any(key.startswith(p) for p in ("DEVTO_", "BLUESKY_", "TWITTER_", "REDDIT_")):
            merged[key] = os.environ[key]

    configured: list[str] = []
    not_configured: list[str] = []

    for platform, guide in PLATFORM_GUIDES.items():
        required = guide["required_keys"]
        if all(merged.get(k) for k in required):
            configured.append(platform)
        else:
            missing = [k for k in required if not merged.get(k)]
            not_configured.append(platform)

    return {"configured": configured, "not_configured": not_configured}


def get_guide(platform: str) -> dict[str, Any]:
    """특정 플랫폼의 API 키 발급 안내를 반환."""
    if platform not in PLATFORM_GUIDES:
        return {"error": f"지원하지 않는 플랫폼: {platform}", "supported": list(PLATFORM_GUIDES)}
    guide = PLATFORM_GUIDES[platform]
    return {
        "platform": platform,
        "url": guide["url"],
        "steps": guide["steps"],
        "required_keys": guide["required_keys"],
    }


def save_credentials(platform: str, credentials: dict[str, str]) -> dict[str, Any]:
    """API 키를 .env에 저장."""
    if platform not in PLATFORM_GUIDES:
        return {"error": f"지원하지 않는 플랫폼: {platform}"}

    required = PLATFORM_GUIDES[platform]["required_keys"]
    missing = [k for k in required if k not in credentials or not credentials[k]]
    if missing:
        return {"error": f"누락된 키: {', '.join(missing)}", "required_keys": required}

    # 기존 .env 로드 후 upsert
    env = _load_env()
    for key, value in credentials.items():
        env[key] = value
        os.environ[key] = value  # 현재 프로세스에도 반영
    _save_env(env)

    return {"saved": True, "platform": platform, "path": str(ENV_PATH)}


async def test_connection(platform: str) -> dict[str, Any]:
    """플랫폼 연결 테스트 (get_trending limit=1)."""
    from devhub.bluesky import Bluesky
    from devhub.devto import DevTo
    from devhub.reddit import Reddit
    from devhub.twitter import Twitter

    adapter_map = {
        "devto": DevTo,
        "bluesky": Bluesky,
        "twitter": Twitter,
        "reddit": Reddit,
    }

    cls = adapter_map.get(platform)
    if cls is None:
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
