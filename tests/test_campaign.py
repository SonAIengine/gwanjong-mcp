"""Campaign 모듈 테스트."""

from __future__ import annotations

from pathlib import Path

from gwanjong_mcp.campaign import CampaignManager
from gwanjong_mcp.storage import ensure_actions_tables, ensure_campaigns_table, get_db


def _make_mgr(tmp_path: Path) -> CampaignManager:
    return CampaignManager(db_path=tmp_path / "test.db")


def test_create_campaign(tmp_path: Path) -> None:
    mgr = _make_mgr(tmp_path)
    camp = mgr.create(
        {
            "name": "MCP Launch Q1",
            "objective": "awareness",
            "topics": ["MCP", "LLM"],
            "platforms": ["devto", "twitter"],
            "icp": "Backend devs building AI tools",
            "cta": "Try graph-tool-call",
            "kpi_target": {"comments": 50, "posts": 10},
        }
    )

    assert camp.id.startswith("camp_")
    assert camp.name == "MCP Launch Q1"
    assert camp.objective == "awareness"
    assert camp.topics == ["MCP", "LLM"]
    assert camp.platforms == ["devto", "twitter"]
    assert camp.status == "active"


def test_list_active_campaigns(tmp_path: Path) -> None:
    mgr = _make_mgr(tmp_path)
    mgr.create({"name": "Active Campaign", "objective": "engagement"})
    mgr.create({"name": "Draft Campaign", "objective": "awareness", "status": "draft"})

    active = mgr.list_active()
    assert len(active) == 2  # active + draft

    all_camps = mgr.list_all()
    assert len(all_camps) == 2


def test_update_campaign_status(tmp_path: Path) -> None:
    mgr = _make_mgr(tmp_path)
    camp = mgr.create({"name": "Test", "objective": "conversion"})

    updated = mgr.update(camp.id, {"status": "paused"})
    assert updated is not None
    assert updated.status == "paused"

    # 존재하지 않는 캠페인
    result = mgr.update("nonexistent", {"status": "active"})
    assert result is None


def test_campaign_report_basic(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    mgr = CampaignManager(db_path=db_path)
    camp = mgr.create(
        {
            "name": "Report Test",
            "objective": "engagement",
            "kpi_target": {"comments": 10},
        }
    )

    # 테스트 actions 삽입 (actions 먼저 생성, campaigns가 campaign_id 컬럼 추가)
    conn = get_db(db_path)
    ensure_actions_tables(conn)
    ensure_campaigns_table(conn)
    conn.execute(
        "INSERT INTO actions (opportunity_id, platform, post_url, action, content, timestamp, campaign_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "opp_1",
            "devto",
            "https://example.com",
            "comment",
            "test",
            "2026-03-15T00:00:00",
            camp.id,
        ),
    )
    conn.execute(
        "INSERT INTO actions (opportunity_id, platform, post_url, action, content, timestamp, campaign_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "opp_2",
            "twitter",
            "https://twitter.com/x",
            "post",
            "tweet",
            "2026-03-15T01:00:00",
            camp.id,
        ),
    )
    conn.commit()
    conn.close()

    report = mgr.get_report(camp.id)
    assert report["total_actions"] == 2
    assert report["by_platform"]["devto"] == 1
    assert report["by_platform"]["twitter"] == 1
    assert report["by_action"]["comment"] == 1
    assert report["kpi_progress"]["comments"]["actual"] == 1
    assert report["kpi_progress"]["comments"]["target"] == 10


def test_campaign_report_not_found(tmp_path: Path) -> None:
    mgr = _make_mgr(tmp_path)
    report = mgr.get_report("nonexistent")
    assert "error" in report


def test_get_campaign(tmp_path: Path) -> None:
    mgr = _make_mgr(tmp_path)
    camp = mgr.create({"name": "Get Test", "objective": "awareness"})

    loaded = mgr.get(camp.id)
    assert loaded is not None
    assert loaded.name == "Get Test"

    assert mgr.get("nonexistent") is None


def test_update_json_fields(tmp_path: Path) -> None:
    mgr = _make_mgr(tmp_path)
    camp = mgr.create({"name": "JSON Test", "objective": "awareness", "topics": ["A"]})

    updated = mgr.update(camp.id, {"topics": ["A", "B", "C"], "kpi_target": {"comments": 100}})
    assert updated is not None
    assert updated.topics == ["A", "B", "C"]
    assert updated.kpi_target == {"comments": 100}
