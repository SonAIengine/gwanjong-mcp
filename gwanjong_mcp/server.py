"""gwanjong-mcp server — PipelineMCP + 5 tools."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp_pipeline import PipelineMCP, State

from . import pipeline, setup
from .events import EventBus
from .memory import Memory
from .safety import Safety
from .tracker import Tracker

logger = logging.getLogger(__name__)

import os

# Load .env (configurable via GWANJONG_ENV_PATH)
_env_path = Path(os.getenv("GWANJONG_ENV_PATH", str(Path.home() / ".gwanjong" / ".env")))
if _env_path.exists():
    load_dotenv(_env_path)


class GwanjongState(State):
    """gwanjong-mcp pipeline state."""

    opportunities: dict[str, Any] = {}
    contexts: dict[str, Any] = {}
    history: list[dict] = []


# 글로벌 EventBus — 플러그인이 attach 가능
bus = EventBus()

# 플러그인 자동 연결
Safety().attach(bus)
Memory().attach(bus)
Tracker().attach(bus)

server = PipelineMCP("gwanjong", state=GwanjongState)


# ── setup ──


@server.tool
async def gwanjong_setup(
    action: str,
    platform: str = "",
    credentials: dict[str, str] | None = None,
    state: GwanjongState | None = None,
) -> dict[str, Any]:
    """Platform onboarding. action: check(status), guide(instructions), save(store keys+test)."""
    if action == "check":
        return setup.check_platforms()

    if action == "guide":
        if not platform:
            return {"error": "platform 필수 (devto, bluesky, twitter, reddit)"}
        return setup.get_guide(platform)

    if action == "save":
        if not platform:
            return {"error": "platform 필수"}
        if not credentials:
            return {"error": "credentials 필수"}
        save_result = setup.save_credentials(platform, credentials)
        if "error" in save_result:
            return save_result
        # 연결 테스트
        test_result = await setup.test_connection(platform)
        return {**save_result, **test_result}

    return {"error": f"알 수 없는 action: {action}", "supported": ["check", "guide", "save"]}


# ── scout ──


@server.tool
async def gwanjong_scout(
    topic: str,
    platforms: list[str] | None = None,
    limit: int = 5,
    state: GwanjongState | None = None,
) -> dict[str, Any]:
    """Scout relevant discussions from developer communities. Returns top scored opportunities."""
    opportunities, response = await pipeline.scout(topic, platforms, limit, bus=bus)
    # state에 직접 저장 (stores 데코레이터 대신 수동 — 반환값은 압축 응답이므로)
    if state is not None:
        state.opportunities = opportunities
    return response


# ── draft ──


@server.tool(requires="opportunities")
async def gwanjong_draft(
    opportunity_id: str,
    state: GwanjongState | None = None,
) -> dict[str, Any]:
    """Gather full context for a specific opportunity. Returns post, comments, and sentiment analysis."""
    if state is None or opportunity_id not in state.opportunities:
        return {"error": f"기회 '{opportunity_id}'를 찾을 수 없음. scout를 먼저 실행하세요."}

    opp = state.opportunities[opportunity_id]
    context, response = await pipeline.draft(opp, bus=bus)

    # contexts에 저장
    if state is not None:
        state.contexts[opportunity_id] = {
            "context": context,
            "post_id": opp.post_id,
        }
    return response


# ── strike ──


@server.tool(requires="contexts")
async def gwanjong_strike(
    opportunity_id: str,
    action: str,
    content: str,
    state: GwanjongState | None = None,
) -> dict[str, Any]:
    """Execute: comment, post, or upvote. Uses context cached from draft."""
    if state is None or opportunity_id not in state.contexts:
        return {"error": f"맥락 '{opportunity_id}'을 찾을 수 없음. draft를 먼저 실행하세요."}

    ctx_data = state.contexts[opportunity_id]
    context = ctx_data["context"]
    post_id = ctx_data["post_id"]

    # DraftContext의 opportunity_id를 실제 post_id로 교체하여 strike 실행
    context.opportunity_id = post_id

    record, response = await pipeline.strike(context, action, content, bus=bus)

    # 이력 기록
    if state is not None:
        state.history.append({
            "opportunity_id": opportunity_id,
            "action": record.action,
            "platform": record.platform,
            "url": record.url,
            "timestamp": record.timestamp,
        })

    return response


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    server.run()


if __name__ == "__main__":
    main()
