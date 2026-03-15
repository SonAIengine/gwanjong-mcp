"""Conversion tracking 모듈 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from gwanjong_mcp.conversion import (
    ConversionTracker,
    generate_utm,
    inject_utm,
)
from gwanjong_mcp.events import Event, EventBus


def test_utm_generation() -> None:
    utm = generate_utm("camp_001", "devto", "comment")
    assert utm["utm_source"] == "devto"
    assert utm["utm_medium"] == "comment"
    assert utm["utm_campaign"] == "camp_001"


def test_utm_injection_in_content() -> None:
    content = "Check this out: https://example.com/tool and https://other.com/page"
    utm = generate_utm("camp_001", "devto", "comment")
    result = inject_utm(content, utm)

    assert "utm_source=devto" in result
    assert "utm_campaign=camp_001" in result
    # 두 URL 모두 태깅
    assert result.count("utm_source=devto") == 2


def test_utm_injection_skips_existing_utm() -> None:
    content = "Link: https://example.com/page?utm_source=existing"
    utm = generate_utm("camp_001", "devto", "comment")
    result = inject_utm(content, utm)

    # 기존 UTM이 있으면 건너뜀
    assert result.count("utm_source") == 1


def test_utm_injection_no_urls() -> None:
    content = "No links here, just text"
    utm = generate_utm("camp_001", "devto", "comment")
    result = inject_utm(content, utm)
    assert result == content


def test_conversion_event_recording(tmp_path: Path) -> None:
    tracker = ConversionTracker(db_path=tmp_path / "test.db")
    conv = tracker.record_event(
        campaign_id="camp_001",
        source="devto",
        medium="comment",
        event_type="click",
        url="https://example.com",
    )

    assert conv.id.startswith("conv_")
    assert conv.campaign_id == "camp_001"
    assert conv.source == "devto"


def test_campaign_conversion_stats(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    tracker = ConversionTracker(db_path=db_path)

    tracker.record_event("camp_001", "devto", "comment", "click")
    tracker.record_event("camp_001", "devto", "comment", "click")
    tracker.record_event("camp_001", "twitter", "post", "star")

    stats = tracker.get_stats("camp_001")
    assert stats["total"] == 3
    assert stats["by_source"]["devto"] == 2
    assert stats["by_source"]["twitter"] == 1
    assert stats["by_type"]["click"] == 2
    assert stats["by_type"]["star"] == 1


@pytest.mark.asyncio
async def test_conversion_tracker_bus_integration(tmp_path: Path) -> None:
    """EventBus 연동 시 campaign_id가 있는 strike.after에서 전환 기록."""
    from types import SimpleNamespace

    db_path = tmp_path / "test.db"
    tracker = ConversionTracker(db_path=db_path)
    bus = EventBus()
    tracker.attach(bus)

    record = SimpleNamespace(
        platform="devto",
        action="comment",
        url="https://dev.to/post#comment",
        opportunity_id="opp_0",
        post_id="123",
        timestamp="2026-03-15T00:00:00",
    )

    await bus.emit(
        Event(
            "strike.after",
            {
                "record": record,
                "response": {"status": "posted"},
                "content": "test comment",
                "campaign_id": "camp_001",
            },
        )
    )

    stats = tracker.get_stats("camp_001")
    assert stats["total"] == 1

    # campaign_id 없으면 기록하지 않음
    await bus.emit(
        Event(
            "strike.after",
            {
                "record": record,
                "response": {"status": "posted"},
                "content": "test comment",
            },
        )
    )
    stats2 = tracker.get_stats("camp_001")
    assert stats2["total"] == 1  # 증가하지 않음
