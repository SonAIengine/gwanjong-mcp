"""gwanjong-mcp 서버 테스트."""

from __future__ import annotations

import pytest
from typing import Any

from gwanjong_mcp.server import server, GwanjongState


def test_tools_registered():
    """5개 tool이 등록되어야 함 (4 custom + _status)."""
    tools = server.mcp._tool_manager._tools
    assert "gwanjong_setup" in tools
    assert "gwanjong_scout" in tools
    assert "gwanjong_draft" in tools
    assert "gwanjong_strike" in tools
    assert "_status" in tools


def test_tool_meta():
    """stores/requires 메타데이터 확인."""
    meta = server._tool_meta
    assert meta["gwanjong_scout"]["stores"] == ["opportunities"]
    assert meta["gwanjong_draft"]["stores"] == ["contexts"]
    assert meta["gwanjong_draft"]["requires"] == ["opportunities"]
    assert meta["gwanjong_strike"]["requires"] == ["contexts"]


def test_state_initialized():
    """GwanjongState가 초기화되어야 함."""
    assert server.state is not None
    assert isinstance(server.state, GwanjongState)
    assert server.state.opportunities == {}
    assert server.state.contexts == {}
    assert server.state.history == []


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
