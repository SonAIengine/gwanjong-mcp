"""autonomous 모듈 테스트."""

from __future__ import annotations

import os
import sys
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from gwanjong_mcp.approval import ApprovalQueue
from gwanjong_mcp.autonomous import AutonomousLoop, CycleConfig, CycleResult
from gwanjong_mcp.events import EventBus
from gwanjong_mcp.types import DraftContext, Opportunity


class DummyLLM:
    async def generate(self, context: DraftContext) -> str:
        return "Generated reply"


def _make_opportunity() -> Opportunity:
    return Opportunity(
        id="opp_0",
        platform="devto",
        post_id="post_123",
        title="Need feedback on MCP servers",
        url="https://dev.to/example/post",
        relevance=0.9,
        comments_count=12,
        reason="주제 직접 관련",
        suggested_actions=["comment"],
    )


@pytest.mark.asyncio
async def test_process_opportunity_enqueues_for_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_draft(opportunity: Opportunity, bus: EventBus | None = None):
        context = DraftContext(
            opportunity_id=opportunity.id,
            platform=opportunity.platform,
            title=opportunity.title,
            body_summary="summary",
            top_comments=["comment 1"],
            tone="technical",
            suggested_approach="comment",
        )
        return context, {"title": opportunity.title}

    async def fake_strike(*args, **kwargs):
        raise AssertionError("strike should not run when approval is required")

    monkeypatch.setattr("gwanjong_mcp.autonomous.pipeline.draft", fake_draft)
    monkeypatch.setattr("gwanjong_mcp.autonomous.pipeline.strike", fake_strike)

    queue = ApprovalQueue(db_path=tmp_path / "approval.db")
    loop = AutonomousLoop(
        bus=EventBus(),
        llm=DummyLLM(),
        config=CycleConfig(require_approval=True, track_replies=False),
        approval_queue=queue,
    )
    result = CycleResult(topic="MCP")

    await loop._process_opportunity(_make_opportunity(), result)

    pending = queue.get_pending()
    assert result.actions_attempted == 0
    assert result.actions_queued == 1
    assert len(pending) == 1
    assert pending[0]["topic"] == "MCP"
    assert pending[0]["platform"] == "devto"
    assert pending[0]["action"] == "comment"
    assert pending[0]["content"] == "Generated reply"
    assert pending[0]["status"] == "pending"


def test_approval_queue_status_updates(tmp_path: Path) -> None:
    queue = ApprovalQueue(db_path=tmp_path / "approval.db")
    opportunity = _make_opportunity()
    context = DraftContext(
        opportunity_id=opportunity.id,
        platform=opportunity.platform,
        title=opportunity.title,
        body_summary="summary",
        suggested_approach="comment",
    )

    item = queue.enqueue(
        topic="MCP",
        opportunity=opportunity,
        context=context,
        action="comment",
        content="Generated reply",
    )
    queue.mark_approved(item.id)

    assert queue.get_pending() == []
    assert queue.stats()["approved"] == 1


def test_approval_queue_reject_requires_pending(tmp_path: Path) -> None:
    queue = ApprovalQueue(db_path=tmp_path / "approval.db")
    opportunity = _make_opportunity()
    context = DraftContext(
        opportunity_id=opportunity.id,
        platform=opportunity.platform,
        title=opportunity.title,
        body_summary="summary",
        suggested_approach="comment",
    )

    item = queue.enqueue(
        topic="MCP",
        opportunity=opportunity,
        context=context,
        action="comment",
        content="Generated reply",
    )
    queue.mark_approved(item.id)

    with pytest.raises(ValueError, match="cannot transition"):
        queue.mark_rejected(item.id)


def test_approval_queue_lists_failed(tmp_path: Path) -> None:
    queue = ApprovalQueue(db_path=tmp_path / "approval.db")
    opportunity = _make_opportunity()
    context = DraftContext(
        opportunity_id=opportunity.id,
        platform=opportunity.platform,
        title=opportunity.title,
        body_summary="summary",
        suggested_approach="comment",
    )
    item = queue.enqueue(
        topic="MCP",
        opportunity=opportunity,
        context=context,
        action="comment",
        content="Generated reply",
    )
    queue._update_status(item.id, "failed")

    failed = queue.get_failed()
    assert len(failed) == 1
    assert failed[0]["id"] == item.id


def test_approval_queue_get_item(tmp_path: Path) -> None:
    queue = ApprovalQueue(db_path=tmp_path / "approval.db")
    opportunity = _make_opportunity()
    context = DraftContext(
        opportunity_id=opportunity.id,
        platform=opportunity.platform,
        title=opportunity.title,
        body_summary="summary",
        suggested_approach="comment",
    )

    item = queue.enqueue(
        topic="MCP",
        opportunity=opportunity,
        context=context,
        action="comment",
        content="Generated reply",
    )

    loaded = queue.get_item(item.id)
    assert loaded is not None
    assert loaded["id"] == item.id
    assert loaded["status"] == "pending"


def test_approval_cli_list_and_approve(tmp_path: Path) -> None:
    db_path = tmp_path / "approval.db"
    queue = ApprovalQueue(db_path=db_path)
    opportunity = _make_opportunity()
    context = DraftContext(
        opportunity_id=opportunity.id,
        platform=opportunity.platform,
        title=opportunity.title,
        body_summary="summary",
        suggested_approach="comment",
    )
    item = queue.enqueue(
        topic="MCP",
        opportunity=opportunity,
        context=context,
        action="comment",
        content="Generated reply",
    )

    env = dict(os.environ)
    env["GWANJONG_DB_PATH"] = str(db_path)
    list_result = subprocess.run(
        [sys.executable, "-m", "gwanjong_mcp.approval_cli", "list"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert f'"id": {item.id}' in list_result.stdout

    show_result = subprocess.run(
        [sys.executable, "-m", "gwanjong_mcp.approval_cli", "show", str(item.id)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert f'"id": {item.id}' in show_result.stdout


@pytest.mark.asyncio
async def test_execute_approved_posts_and_updates_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    queue = ApprovalQueue(db_path=tmp_path / "approval.db")
    opportunity = _make_opportunity()
    context = DraftContext(
        opportunity_id=opportunity.id,
        platform=opportunity.platform,
        title=opportunity.title,
        body_summary="summary",
        suggested_approach="comment",
    )
    item = queue.enqueue(
        topic="MCP",
        opportunity=opportunity,
        context=context,
        action="comment",
        content="Generated reply",
    )

    async def fake_strike(ctx, action, content, bus=None):
        assert ctx.opportunity_id == opportunity.post_id
        assert action == "comment"
        assert content == "Generated reply"
        return (
            SimpleNamespace(
                action="comment",
                platform=opportunity.platform,
                opportunity_id=opportunity.post_id,
                url="https://dev.to/example/post#comment",
                timestamp="2026-03-13T00:00:00+00:00",
            ),
            {"status": "posted", "url": "https://dev.to/example/post#comment", "platform": "devto"},
        )

    monkeypatch.setattr("gwanjong_mcp.approval.pipeline.strike", fake_strike)

    result = await queue.execute_approved(item.id, bus=EventBus())

    stored = queue.get_item(item.id)
    assert result["queue_status"] == "posted"
    assert stored is not None
    assert stored["status"] == "posted"
    assert stored["executed_at"] is not None
    assert stored["last_error"] is None


@pytest.mark.asyncio
async def test_execute_approved_failure_marks_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    queue = ApprovalQueue(db_path=tmp_path / "approval.db")
    opportunity = _make_opportunity()
    context = DraftContext(
        opportunity_id=opportunity.id,
        platform=opportunity.platform,
        title=opportunity.title,
        body_summary="summary",
        suggested_approach="comment",
    )
    item = queue.enqueue(
        topic="MCP",
        opportunity=opportunity,
        context=context,
        action="comment",
        content="Generated reply",
    )

    async def fake_strike(ctx, action, content, bus=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("gwanjong_mcp.approval.pipeline.strike", fake_strike)

    with pytest.raises(RuntimeError, match="boom"):
        await queue.execute_approved(item.id, bus=EventBus())

    stored = queue.get_item(item.id)
    assert stored is not None
    assert stored["status"] == "failed"
    assert stored["executed_at"] is not None
    assert stored["last_error"] == "boom"


@pytest.mark.asyncio
async def test_execute_approved_prevents_second_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    queue = ApprovalQueue(db_path=tmp_path / "approval.db")
    opportunity = _make_opportunity()
    context = DraftContext(
        opportunity_id=opportunity.id,
        platform=opportunity.platform,
        title=opportunity.title,
        body_summary="summary",
        suggested_approach="comment",
    )
    item = queue.enqueue(
        topic="MCP",
        opportunity=opportunity,
        context=context,
        action="comment",
        content="Generated reply",
    )

    async def fake_strike(ctx, action, content, bus=None):
        return (
            SimpleNamespace(
                action="comment",
                platform=opportunity.platform,
                opportunity_id=opportunity.post_id,
                url="https://dev.to/example/post#comment",
                timestamp="2026-03-13T00:00:00+00:00",
            ),
            {"status": "posted", "url": "https://dev.to/example/post#comment", "platform": "devto"},
        )

    monkeypatch.setattr("gwanjong_mcp.approval.pipeline.strike", fake_strike)

    first = await queue.execute_approved(item.id, bus=EventBus())
    assert first["queue_status"] == "posted"

    with pytest.raises(ValueError, match="not executable: posted"):
        await queue.execute_approved(item.id, bus=EventBus())


@pytest.mark.asyncio
async def test_retry_failed_reexecutes_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    queue = ApprovalQueue(db_path=tmp_path / "approval.db")
    opportunity = _make_opportunity()
    context = DraftContext(
        opportunity_id=opportunity.id,
        platform=opportunity.platform,
        title=opportunity.title,
        body_summary="summary",
        suggested_approach="comment",
    )
    item = queue.enqueue(
        topic="MCP",
        opportunity=opportunity,
        context=context,
        action="comment",
        content="Generated reply",
    )
    queue._update_status(item.id, "failed", last_error="boom")

    async def fake_strike(ctx, action, content, bus=None):
        return (
            SimpleNamespace(
                action="comment",
                platform=opportunity.platform,
                opportunity_id=opportunity.post_id,
                url="https://dev.to/example/post#comment",
                timestamp="2026-03-13T00:00:00+00:00",
            ),
            {"status": "posted", "url": "https://dev.to/example/post#comment", "platform": "devto"},
        )

    monkeypatch.setattr("gwanjong_mcp.approval.pipeline.strike", fake_strike)

    result = await queue.retry_failed(item.id, bus=EventBus())
    stored = queue.get_item(item.id)
    assert result["queue_status"] == "posted"
    assert stored is not None
    assert stored["status"] == "posted"
    assert stored["last_error"] is None
