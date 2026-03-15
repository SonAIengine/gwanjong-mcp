"""EventBus — Pub/sub for loose coupling between modules."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class Blocked(Exception):
    """Raised when a handler blocks a *.before event."""


@dataclass
class Event:
    """Event data."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


# 핸들러 타입: async 함수, Event를 받고 Any를 반환
Handler = Callable[[Event], Awaitable[Any]]


class EventBus:
    """Simple async pub/sub. No dependencies.

    - Regular events: all handlers are executed, return values ignored.
    - *.before events: if a handler returns False, a Blocked exception is raised.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def on(self, event_type: str, handler: Handler) -> None:
        """Register an event handler."""
        self._handlers.setdefault(event_type, []).append(handler)

    def off(self, event_type: str, handler: Handler) -> None:
        """Remove an event handler."""
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: Event) -> None:
        """Emit an event. *.before events can be blocked."""
        handlers = self._handlers.get(event.type, [])
        is_before = event.type.endswith(".before")

        for handler in handlers:
            try:
                result = await handler(event)
                if is_before:
                    if isinstance(result, str) and result:
                        raise Blocked(result)
                    if result is False:
                        raise Blocked(f"{event.type} blocked by {handler.__qualname__}")
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
        """Total number of registered handlers."""
        return sum(len(hs) for hs in self._handlers.values())
