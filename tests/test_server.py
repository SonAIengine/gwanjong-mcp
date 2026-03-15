"""gwanjong-mcp 서버 테스트."""

from __future__ import annotations

import pytest

from gwanjong_mcp.events import Blocked, Event, EventBus
from gwanjong_mcp.safety import Safety
from gwanjong_mcp.server import GwanjongState, server
from gwanjong_mcp.types import DraftContext, Opportunity


def test_tools_registered():
    """8개 tool이 등록되어야 함 (7 custom + _status)."""
    tools = server.mcp._tool_manager._tools
    assert "gwanjong_setup" in tools
    assert "gwanjong_scout" in tools
    assert "gwanjong_draft" in tools
    assert "gwanjong_strike" in tools
    assert "gwanjong_campaign" in tools
    assert "gwanjong_assets" in tools
    assert "gwanjong_schedule" in tools
    assert "_status" in tools


def test_tool_meta():
    """requires/stores 메타데이터 확인."""
    meta = server._tool_meta
    assert meta["gwanjong_scout"]["stores"] == ["opportunities"]
    assert meta["gwanjong_draft"]["requires"] == ["opportunities"]
    assert meta["gwanjong_draft"]["stores"] == ["contexts"]
    assert meta["gwanjong_strike"]["requires"] == ["contexts"]
    assert meta["gwanjong_strike"]["stores"] == ["history"]
    assert meta["gwanjong_campaign"]["stores"] == ["campaigns"]
    assert meta["gwanjong_assets"]["stores"] == ["assets"]
    assert meta["gwanjong_schedule"]["requires"] == ["campaigns"]


def test_state_initialized():
    """GwanjongState가 초기화되어야 함."""
    assert server.state is not None
    assert isinstance(server.state, GwanjongState)
    assert server.state.opportunities == {}
    assert server.state.contexts == {}
    assert server.state.history == []
    assert server.state.campaigns == {}
    assert server.state.assets == {}


@pytest.mark.asyncio
async def test_status_shows_blocked():
    """_status가 blocked tools를 올바르게 표시."""
    tools = server.mcp._tool_manager._tools
    result = await tools["_status"].fn()

    assert "state" in result
    assert "tools" in result
    # opportunities가 비어있으므로 draft는 blocked
    blocked_names = [b["tool"] for b in result["tools"]["blocked"]]
    assert "gwanjong_draft" in blocked_names


@pytest.mark.asyncio
async def test_draft_requires_scout_first():
    """scout 없이 draft 호출하면 에러."""
    tools = server.mcp._tool_manager._tools
    # state 초기화
    server.state.opportunities = {}
    result = await tools["gwanjong_draft"].fn(opportunity_id="opp_0")
    assert "error" in result or "missing" in result


@pytest.mark.asyncio
async def test_setup_check():
    """setup check action 테스트."""
    tools = server.mcp._tool_manager._tools
    result = await tools["gwanjong_setup"].fn(action="check")
    assert "configured" in result
    assert "not_configured" in result


@pytest.mark.asyncio
async def test_setup_guide():
    """setup guide action 테스트."""
    tools = server.mcp._tool_manager._tools
    result = await tools["gwanjong_setup"].fn(action="guide", platform="devto")
    assert result["platform"] == "devto"
    assert "steps" in result
    assert "required_keys" in result


@pytest.mark.asyncio
async def test_setup_invalid_action():
    """setup 잘못된 action 테스트."""
    tools = server.mcp._tool_manager._tools
    result = await tools["gwanjong_setup"].fn(action="invalid")
    assert "error" in result


@pytest.mark.asyncio
async def test_scout_filters_acted_opportunities(monkeypatch: pytest.MonkeyPatch):
    """MCP scout도 이미 활동한 게시글을 제외해야 함."""

    async def fake_scout(topic, platforms=None, limit=5, bus=None):
        opportunities = {
            "opp_0": Opportunity(
                id="opp_0",
                platform="devto",
                post_id="post_0",
                title="Already acted",
                url="https://example.com/acted",
                relevance=0.9,
                comments_count=12,
                reason="test",
                suggested_actions=["comment"],
            ),
            "opp_1": Opportunity(
                id="opp_1",
                platform="devto",
                post_id="post_1",
                title="Fresh post",
                url="https://example.com/fresh",
                relevance=0.8,
                comments_count=8,
                reason="test",
                suggested_actions=["comment"],
            ),
        }
        response = {
            "opportunities": [
                {"id": "opp_0", "title": "Already acted"},
                {"id": "opp_1", "title": "Fresh post"},
            ],
            "total_scanned": 2,
            "summary": "1개 플랫폼에서 2건의 기회 발견",
        }
        return opportunities, response

    def fake_filter_unseen(self, opportunities):
        return {"opp_1": opportunities["opp_1"]}

    monkeypatch.setattr("gwanjong_mcp.server.pipeline.scout", fake_scout)
    monkeypatch.setattr("gwanjong_mcp.server.Memory.filter_unseen", fake_filter_unseen)

    server.state.opportunities = {}
    tools = server.mcp._tool_manager._tools
    result = await tools["gwanjong_scout"].fn(topic="MCP")

    assert [item["id"] for item in result["opportunities"]] == ["opp_1"]
    assert result["deduped_acted"] == 1
    assert list(server.state.opportunities.keys()) == ["opp_1"]


@pytest.mark.asyncio
async def test_strike_returns_block_reason(monkeypatch: pytest.MonkeyPatch):
    """차단 사유가 구조화된 응답으로 내려와야 함."""

    async def fake_strike(context, action, content, bus=None):
        raise Blocked("devto cooldown active (30min remaining)")

    monkeypatch.setattr("gwanjong_mcp.server.pipeline.strike", fake_strike)

    tools = server.mcp._tool_manager._tools
    server.state.contexts = {
        "opp_0": {
            "context": DraftContext(
                opportunity_id="opp_0",
                platform="devto",
                title="Need feedback",
                body_summary="summary",
                suggested_approach="comment",
            ),
            "post_id": "post_123",
        }
    }

    result = await tools["gwanjong_strike"].fn(
        opportunity_id="opp_0",
        action="comment",
        content="content",
    )

    assert result["blocked"] is True
    assert result["error"] == "devto cooldown active (30min remaining)"


@pytest.mark.asyncio
async def test_eventbus_propagates_safety_reason(monkeypatch: pytest.MonkeyPatch):
    """Safety가 계산한 차단 이유가 Blocked 메시지로 전달되어야 함."""
    bus = EventBus()
    safety = Safety()
    safety.attach(bus)

    monkeypatch.setattr(
        safety,
        "check_rate_limit",
        lambda platform, action: (False, "devto cooldown active (30min remaining)"),
    )

    with pytest.raises(Blocked, match="cooldown active"):
        await bus.emit(
            Event(
                "strike.before",
                {"platform": "devto", "action": "comment", "content": "hello"},
            )
        )
