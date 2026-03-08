"""EventBus — 모듈 간 느슨한 결합을 위한 pub/sub."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class Blocked(Exception):
    """*.before 이벤트에서 핸들러가 차단했을 때 발생."""


@dataclass
class Event:
    """이벤트 데이터."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


# 핸들러 타입: async 함수, Event를 받고 Any를 반환
Handler = Callable[[Event], Awaitable[Any]]


class EventBus:
    """단순 async pub/sub. 의존성 없음.

    - 일반 이벤트: 모든 핸들러 실행, 반환값 무시
    - *.before 이벤트: 핸들러가 False를 반환하면 Blocked 예외 발생 (차단)
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def on(self, event_type: str, handler: Handler) -> None:
        """이벤트 핸들러 등록."""
        self._handlers.setdefault(event_type, []).append(handler)

    def off(self, event_type: str, handler: Handler) -> None:
        """이벤트 핸들러 제거."""
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: Event) -> None:
        """이벤트 발행. *.before 이벤트는 차단 가능."""
        handlers = self._handlers.get(event.type, [])
        is_before = event.type.endswith(".before")

        for handler in handlers:
            try:
                result = await handler(event)
                if is_before and result is False:
                    raise Blocked(
                        f"{event.type} blocked by {handler.__qualname__}"
                    )
            except Blocked:
                raise
            except Exception:
                logger.error(
                    "이벤트 핸들러 에러: %s in %s",
                    event.type,
                    handler.__qualname__,
                    exc_info=True,
                )

    @property
    def handler_count(self) -> int:
        """등록된 핸들러 총 수."""
        return sum(len(hs) for hs in self._handlers.values())
