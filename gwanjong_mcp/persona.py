"""페르소나 관리 — 플랫폼별 톤/스타일 설정. 독립 모듈."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".gwanjong" / "persona.json"

# 기본 페르소나 (persona.json 없을 때 사용)
_DEFAULTS: dict[str, dict[str, Any]] = {
    "devto": {
        "tone": "casual-professional",
        "style": "코드 예시를 자주 포함, 실무 경험 기반",
        "max_length": 500,
        "language": "en",
    },
    "bluesky": {
        "tone": "conversational",
        "style": "빌드 인 퍼블릭, 일상적 개발 이야기",
        "max_length": 300,
        "language": "en",
    },
    "twitter": {
        "tone": "punchy",
        "style": "한 줄 인사이트, 해시태그 2-3개",
        "max_length": 280,
        "language": "en",
    },
    "reddit": {
        "tone": "blunt-helpful",
        "style": "짧고 직접적, 링크 최소화",
        "max_length": 300,
        "language": "en",
    },
}


@dataclass
class Persona:
    """플랫폼별 페르소나."""

    platform: str
    tone: str = "neutral"
    style: str = ""
    max_length: int = 500
    language: str = "en"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_system_prompt(self) -> str:
        """LLM 시스템 프롬프트용 페르소나 설명 생성."""
        parts = [
            f"Platform: {self.platform}",
            f"Tone: {self.tone}",
            f"Max length: {self.max_length} chars",
            f"Language: {self.language}",
        ]
        if self.style:
            parts.append(f"Style: {self.style}")
        return "\n".join(parts)


class PersonaManager:
    """페르소나 로딩/관리. 파일 I/O만 의존."""

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self._config_path = config_path
        self._personas: dict[str, Persona] = {}
        self._identity: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """persona.json 로드. 없으면 기본값 사용."""
        if self._config_path.exists():
            try:
                data = json.loads(self._config_path.read_text())
                self._identity = data.get("identity", {})
                for platform, config in data.get("personas", {}).items():
                    self._personas[platform] = Persona(
                        platform=platform,
                        tone=config.get("tone", "neutral"),
                        style=config.get("style", ""),
                        max_length=config.get("max_length", 500),
                        language=config.get("language", "en"),
                        extra={k: v for k, v in config.items()
                               if k not in ("tone", "style", "max_length", "language")},
                    )
                logger.info("Persona loaded from %s (%d platforms)", self._config_path, len(self._personas))
                return
            except Exception:
                logger.warning("persona.json 로드 실패, 기본값 사용", exc_info=True)

        # 기본값
        for platform, config in _DEFAULTS.items():
            self._personas[platform] = Persona(platform=platform, **config)

    def get(self, platform: str) -> Persona:
        """플랫폼별 페르소나 반환. 없으면 기본값."""
        if platform in self._personas:
            return self._personas[platform]
        return Persona(platform=platform)

    @property
    def identity(self) -> dict[str, Any]:
        """사용자 아이덴티티 정보."""
        return self._identity

    @property
    def platforms(self) -> list[str]:
        """설정된 플랫폼 목록."""
        return list(self._personas.keys())
