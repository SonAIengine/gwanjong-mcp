"""Scheduler 모듈 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from gwanjong_mcp.scheduler import Scheduler


def _make_sched(tmp_path: Path) -> Scheduler:
    return Scheduler(db_path=tmp_path / "test.db")


def test_add_and_list(tmp_path: Path) -> None:
    sched = _make_sched(tmp_path)
    item = sched.add(
        {
            "campaign_id": "camp_001",
            "platform": "devto",
            "action": "post",
            "content": "Scheduled post content",
            "scheduled_at": "2026-03-20T09:00:00+00:00",
        }
    )

    assert item.id.startswith("sched_")
    assert item.status == "pending"
    assert item.platform == "devto"

    pending = sched.list_pending()
    assert len(pending) == 1
    assert pending[0].id == item.id


def test_cancel(tmp_path: Path) -> None:
    sched = _make_sched(tmp_path)
    item = sched.add(
        {
            "campaign_id": "camp_001",
            "platform": "twitter",
            "content": "tweet",
            "scheduled_at": "2026-03-20T09:00:00+00:00",
        }
    )

    assert sched.cancel(item.id) is True
    assert sched.list_pending() == []

    # 이미 취소된 항목
    assert sched.cancel(item.id) is False


def test_check_due(tmp_path: Path) -> None:
    sched = _make_sched(tmp_path)

    # 과거 시간 (due)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    sched.add(
        {
            "campaign_id": "camp_001",
            "platform": "devto",
            "content": "past content",
            "scheduled_at": past,
        }
    )

    # 미래 시간 (not due)
    future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    sched.add(
        {
            "campaign_id": "camp_001",
            "platform": "twitter",
            "content": "future content",
            "scheduled_at": future,
        }
    )

    due = sched.check_due()
    assert len(due) == 1
    assert due[0].platform == "devto"


def test_list_all(tmp_path: Path) -> None:
    sched = _make_sched(tmp_path)
    sched.add(
        {
            "campaign_id": "camp_001",
            "platform": "devto",
            "content": "a",
            "scheduled_at": "2026-03-20T09:00:00+00:00",
        }
    )
    sched.add(
        {
            "campaign_id": "camp_002",
            "platform": "twitter",
            "content": "b",
            "scheduled_at": "2026-03-21T09:00:00+00:00",
        }
    )

    all_items = sched.list_all()
    assert len(all_items) == 2

    filtered = sched.list_all(campaign_id="camp_001")
    assert len(filtered) == 1


def test_list_pending_by_campaign(tmp_path: Path) -> None:
    sched = _make_sched(tmp_path)
    sched.add(
        {
            "campaign_id": "camp_001",
            "platform": "devto",
            "content": "a",
            "scheduled_at": "2026-03-20T09:00:00+00:00",
        }
    )
    sched.add(
        {
            "campaign_id": "camp_002",
            "platform": "twitter",
            "content": "b",
            "scheduled_at": "2026-03-21T09:00:00+00:00",
        }
    )

    pending = sched.list_pending(campaign_id="camp_001")
    assert len(pending) == 1
    assert pending[0].campaign_id == "camp_001"
