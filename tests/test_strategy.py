"""Strategy 엔진 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from gwanjong_mcp.campaign import CampaignManager
from gwanjong_mcp.storage import ensure_actions_tables, ensure_campaigns_table, get_db
from gwanjong_mcp.strategy import StrategyEngine


def _setup(tmp_path: Path) -> tuple[StrategyEngine, str]:
    """Create strategy engine with a campaign."""
    db_path = tmp_path / "test.db"

    # actions 테이블을 먼저 생성
    pre_conn = get_db(db_path)
    ensure_actions_tables(pre_conn)
    pre_conn.close()

    mgr = CampaignManager(db_path=db_path)
    camp = mgr.create(
        {
            "name": "Strategy Test",
            "objective": "awareness",
            "topics": ["MCP", "LLM", "AI Agents"],
            "platforms": ["devto", "twitter"],
        }
    )

    # 테스트 데이터
    conn = get_db(db_path)
    ensure_actions_tables(conn)
    ensure_campaigns_table(conn)
    for i in range(5):
        conn.execute(
            "INSERT INTO actions (opportunity_id, platform, post_url, action, content, timestamp, campaign_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"opp_{i}",
                "devto",
                f"https://dev.to/post{i}",
                "comment",
                "test",
                f"2026-03-1{i}T10:00:00",
                camp.id,
            ),
        )
    for i in range(2):
        conn.execute(
            "INSERT INTO actions (opportunity_id, platform, post_url, action, content, timestamp, campaign_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"opp_tw_{i}",
                "twitter",
                f"https://twitter.com/{i}",
                "post",
                "tweet",
                f"2026-03-1{i}T11:00:00",
                camp.id,
            ),
        )
    conn.commit()
    conn.close()

    return StrategyEngine(db_path=db_path), camp.id


@pytest.mark.asyncio
async def test_generate_weekly_plan(tmp_path: Path) -> None:
    engine, camp_id = _setup(tmp_path)
    plan = await engine.generate_weekly_plan(camp_id)

    assert plan["campaign_id"] == camp_id
    assert "suggestions" in plan
    assert len(plan["suggestions"]) > 0
    assert "platform_allocation" in plan
    assert "topic_rotation" in plan


@pytest.mark.asyncio
async def test_generate_weekly_plan_not_found(tmp_path: Path) -> None:
    engine, _ = _setup(tmp_path)
    plan = await engine.generate_weekly_plan("nonexistent")
    assert "error" in plan


def test_suggest_topic_rotation(tmp_path: Path) -> None:
    engine, camp_id = _setup(tmp_path)
    topics = engine.suggest_topic_rotation(camp_id)
    assert topics == ["MCP", "LLM", "AI Agents"]


def test_suggest_platform_allocation(tmp_path: Path) -> None:
    engine, camp_id = _setup(tmp_path)
    allocation = engine.suggest_platform_allocation(camp_id)

    assert "devto" in allocation
    assert "twitter" in allocation
    # devto가 더 활발하므로 +1
    assert allocation["devto"] >= allocation["twitter"]


def test_auto_approve_low_risk(tmp_path: Path) -> None:
    engine, camp_id = _setup(tmp_path)
    plan = {
        "campaign_id": camp_id,
        "platform_allocation": {"devto": 3, "twitter": 2},
        "topic_rotation": ["MCP", "LLM"],
    }

    items = engine.auto_approve_low_risk(plan)
    assert len(items) > 0

    # 모두 comment (low-risk)
    for item in items:
        assert item.action == "comment"
        assert item.status == "pending"


def test_auto_approve_empty_plan(tmp_path: Path) -> None:
    engine, _ = _setup(tmp_path)
    items = engine.auto_approve_low_risk({"campaign_id": "", "platform_allocation": {}})
    assert items == []
