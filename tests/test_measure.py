"""Measurement 모듈 테스트."""

from __future__ import annotations

from pathlib import Path

from gwanjong_mcp.campaign import CampaignManager
from gwanjong_mcp.conversion import ConversionTracker
from gwanjong_mcp.measure import Measurement
from gwanjong_mcp.storage import ensure_actions_tables, ensure_campaigns_table, get_db


def _setup(tmp_path: Path) -> tuple[Measurement, str]:
    """Create measurement instance with a campaign and test data."""
    db_path = tmp_path / "test.db"

    # actions 테이블을 먼저 생성해야 campaigns_table에서 campaign_id 컬럼 추가 가능
    pre_conn = get_db(db_path)
    ensure_actions_tables(pre_conn)
    pre_conn.close()

    mgr = CampaignManager(db_path=db_path)
    camp = mgr.create(
        {
            "name": "Test Campaign",
            "objective": "engagement",
            "topics": ["MCP"],
            "platforms": ["devto", "twitter"],
        }
    )

    conn = get_db(db_path)
    ensure_actions_tables(conn)
    ensure_campaigns_table(conn)
    conn.execute(
        "INSERT INTO actions (opportunity_id, platform, post_url, action, content, timestamp, campaign_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "opp_1",
            "devto",
            "https://dev.to/post1",
            "comment",
            "test",
            "2026-03-14T10:00:00",
            camp.id,
        ),
    )
    conn.execute(
        "INSERT INTO actions (opportunity_id, platform, post_url, action, content, timestamp, campaign_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "opp_2",
            "twitter",
            "https://twitter.com/post1",
            "post",
            "tweet",
            "2026-03-14T11:00:00",
            camp.id,
        ),
    )
    conn.execute(
        "INSERT INTO actions (opportunity_id, platform, post_url, action, content, timestamp, campaign_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "opp_3",
            "devto",
            "https://dev.to/post2",
            "comment",
            "test2",
            "2026-03-15T10:00:00",
            camp.id,
        ),
    )
    conn.commit()
    conn.close()

    tracker = ConversionTracker(db_path=db_path)
    tracker.record_event(camp.id, "devto", "comment", "click")
    tracker.record_event(camp.id, "twitter", "post", "star")

    return Measurement(db_path=db_path), camp.id


def test_campaign_attribution(tmp_path: Path) -> None:
    measure, camp_id = _setup(tmp_path)
    attr = measure.campaign_attribution(camp_id)

    assert attr["campaign_id"] == camp_id
    assert attr["actions_by_platform"]["devto"]["comment"] == 2
    assert attr["actions_by_platform"]["twitter"]["post"] == 1
    assert attr["conversions_by_source"]["devto"]["comment"] == 1


def test_action_performance(tmp_path: Path) -> None:
    measure, camp_id = _setup(tmp_path)
    perf = measure.action_performance(camp_id)

    assert perf["total_comments"] == 2
    assert perf["total_posts"] == 1


def test_weekly_report(tmp_path: Path) -> None:
    measure, camp_id = _setup(tmp_path)
    report = measure.weekly_report(camp_id)

    assert report["campaign_id"] == camp_id
    assert "period" in report
    assert "daily_trend" in report
    assert len(report["daily_trend"]) == 7


def test_best_performing(tmp_path: Path) -> None:
    measure, camp_id = _setup(tmp_path)
    best = measure.best_performing(camp_id, "actions")

    assert len(best) > 0
    assert best[0]["platform"] == "devto"  # 2건으로 최다


def test_ab_lifecycle(tmp_path: Path) -> None:
    measure, camp_id = _setup(tmp_path)

    exp = measure.ab_create(camp_id, "Hook Test", "Hook A", "Hook B")
    assert exp["status"] == "running"

    result = measure.ab_result(exp["id"])
    assert result["name"] == "Hook Test"
    assert result["status"] == "running"

    concluded = measure.ab_conclude(exp["id"], {"winner": "Hook A", "lift": 15.0})
    assert concluded["status"] == "completed"
    assert concluded["result"]["winner"] == "Hook A"


def test_ab_result_not_found(tmp_path: Path) -> None:
    measure, _ = _setup(tmp_path)
    result = measure.ab_result("nonexistent")
    assert "error" in result
