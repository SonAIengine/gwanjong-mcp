"""gwanjong-mcp server — PipelineMCP + 8 tools."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp_pipeline import PipelineMCP, State

from . import pipeline, setup
from .asset import AssetLibrary
from .campaign import CampaignManager
from .conversion import ConversionTracker
from .events import Blocked, EventBus
from .memory import Memory
from .safety import Safety
from .scheduler import Scheduler
from .storage import ensure_indexes, get_db
from .tracker import Tracker

logger = logging.getLogger(__name__)

# Load .env (configurable via GWANJONG_ENV_PATH)
_env_path = Path(os.getenv("GWANJONG_ENV_PATH", str(Path.home() / ".gwanjong" / ".env")))
if _env_path.exists():
    load_dotenv(_env_path)


class GwanjongState(State):
    """gwanjong-mcp pipeline state."""

    opportunities: dict[str, Any] = {}
    contexts: dict[str, Any] = {}
    history: list[dict] = []
    campaigns: dict[str, Any] = {}
    assets: dict[str, Any] = {}


# 글로벌 EventBus — 플러그인이 attach 가능
bus = EventBus()

# 플러그인 자동 연결
Safety().attach(bus)
Memory().attach(bus)
Tracker().attach(bus)
ConversionTracker().attach(bus)

# DB 인덱스 보장 (서버 시작 시 1회)
_init_conn = get_db()
ensure_indexes(_init_conn)
_init_conn.close()

server = PipelineMCP("gwanjong", state=GwanjongState)


def _stored_value(result: tuple[Any, dict[str, Any]]) -> Any:
    """Extract the value that should be persisted in PipelineMCP state."""
    return result[0]


def _response_value(result: tuple[Any, dict[str, Any]]) -> dict[str, Any]:
    """Extract the compressed response sent back to the MCP client."""
    return result[1]


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
            return {"error": "platform 필수"}
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


@server.tool(stores="opportunities", store_value=_stored_value, return_value=_response_value)
async def gwanjong_scout(
    topic: str,
    platforms: list[str] | None = None,
    limit: int = 5,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Scout relevant discussions from developer communities. Returns top scored opportunities."""
    opportunities, response = await pipeline.scout(topic, platforms, limit, bus=bus)
    filtered = Memory().filter_unseen(opportunities)
    filtered_out = len(opportunities) - len(filtered)
    if filtered_out > 0:
        allowed_ids = set(filtered)
        response = dict(response)
        response["opportunities"] = [
            opp for opp in response.get("opportunities", []) if opp.get("id") in allowed_ids
        ]
        response["deduped_acted"] = filtered_out
        response["summary"] = f"{response['summary']} (이미 활동한 {filtered_out}건 제외)"
    return filtered, response


# ── draft ──


@server.tool(
    stores="contexts",
    requires="opportunities",
    store_value=_stored_value,
    return_value=_response_value,
)
async def gwanjong_draft(
    opportunity_id: str,
    state: GwanjongState | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Gather full context for a specific opportunity. Returns post, comments, and sentiment analysis."""
    if state is None or opportunity_id not in state.opportunities:
        error = {"error": f"기회 '{opportunity_id}'를 찾을 수 없음. scout를 먼저 실행하세요."}
        return state.contexts if state is not None else {}, error

    opp = state.opportunities[opportunity_id]
    context, response = await pipeline.draft(opp, bus=bus)
    next_contexts = dict(state.contexts)
    next_contexts[opportunity_id] = {
        "context": context,
        "post_id": opp.post_id,
    }
    return next_contexts, response


# ── strike ──


@server.tool(
    stores="history",
    requires="contexts",
    store_value=_stored_value,
    return_value=_response_value,
)
async def gwanjong_strike(
    opportunity_id: str,
    action: str,
    content: str,
    state: GwanjongState | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute: comment, post, or upvote. Uses context cached from draft."""
    if state is None or opportunity_id not in state.contexts:
        error = {"error": f"맥락 '{opportunity_id}'을 찾을 수 없음. draft를 먼저 실행하세요."}
        return state.history if state is not None else [], error

    ctx_data = state.contexts[opportunity_id]
    context = ctx_data["context"]
    post_id = ctx_data["post_id"]

    if not context.post_id:
        context.post_id = post_id

    try:
        record, response = await pipeline.strike(context, action, content, bus=bus)
    except Blocked as exc:
        return state.history, {
            "error": str(exc),
            "blocked": True,
            "platform": context.platform,
            "action": action,
        }

    next_history = [
        *state.history,
        {
            "opportunity_id": opportunity_id,
            "post_id": getattr(record, "post_id", ""),
            "action": record.action,
            "platform": record.platform,
            "url": record.url,
            "timestamp": record.timestamp,
        },
    ]
    return next_history, response


# ── campaign ──


_campaign_mgr = CampaignManager()


@server.tool(stores="campaigns")
async def gwanjong_campaign(
    action: str,
    campaign_id: str = "",
    data: dict[str, Any] | None = None,
    state: GwanjongState | None = None,
) -> dict[str, Any]:
    """Campaign management. action: create, list, get, update, report."""
    if action == "create":
        if not data or "name" not in data:
            return {"error": "data.name 필수"}
        camp = _campaign_mgr.create(data)
        if state is not None:
            state.campaigns[camp.id] = camp
        return {
            "id": camp.id,
            "name": camp.name,
            "status": camp.status,
            "objective": camp.objective,
        }

    if action == "list":
        camps = _campaign_mgr.list_active()
        return {
            "campaigns": [
                {"id": c.id, "name": c.name, "status": c.status, "objective": c.objective}
                for c in camps
            ],
            "count": len(camps),
        }

    if action == "get":
        if not campaign_id:
            return {"error": "campaign_id 필수"}
        camp = _campaign_mgr.get(campaign_id)
        if not camp:
            return {"error": f"캠페인 '{campaign_id}' 없음"}
        return {
            "id": camp.id,
            "name": camp.name,
            "objective": camp.objective,
            "topics": camp.topics,
            "platforms": camp.platforms,
            "icp": camp.icp,
            "cta": camp.cta,
            "kpi_target": camp.kpi_target,
            "status": camp.status,
            "start_date": camp.start_date,
            "end_date": camp.end_date,
        }

    if action == "update":
        if not campaign_id:
            return {"error": "campaign_id 필수"}
        if not data:
            return {"error": "data 필수"}
        camp = _campaign_mgr.update(campaign_id, data)
        if not camp:
            return {"error": f"캠페인 '{campaign_id}' 없음"}
        return {"id": camp.id, "name": camp.name, "status": camp.status, "updated": True}

    if action == "report":
        if not campaign_id:
            return {"error": "campaign_id 필수"}
        return _campaign_mgr.get_report(campaign_id)

    return {
        "error": f"알 수 없는 action: {action}",
        "supported": ["create", "list", "get", "update", "report"],
    }


# ── assets ──


_asset_lib = AssetLibrary()


@server.tool(stores="assets")
async def gwanjong_assets(
    action: str,
    asset_id: str = "",
    data: dict[str, Any] | None = None,
    state: GwanjongState | None = None,
) -> dict[str, Any]:
    """Asset library. action: save, search, list, use."""
    if action == "save":
        if not data or "content" not in data:
            return {"error": "data.content 필수"}
        asset = _asset_lib.save(data)
        if state is not None:
            state.assets[asset.id] = asset
        return {
            "id": asset.id,
            "asset_type": asset.asset_type,
            "tags": asset.tags,
        }

    if action == "search":
        assets = _asset_lib.search(
            query=data.get("query", "") if data else "",
            asset_type=data.get("asset_type", "") if data else "",
            platform=data.get("platform", "") if data else "",
            campaign_id=data.get("campaign_id", "") if data else "",
        )
        return {
            "assets": [
                {
                    "id": a.id,
                    "type": a.asset_type,
                    "content": a.content[:100],
                    "usage_count": a.usage_count,
                }
                for a in assets
            ],
            "count": len(assets),
        }

    if action == "list":
        assets = _asset_lib.list_top()
        return {
            "assets": [
                {
                    "id": a.id,
                    "type": a.asset_type,
                    "content": a.content[:100],
                    "usage_count": a.usage_count,
                }
                for a in assets
            ],
            "count": len(assets),
        }

    if action == "use":
        if not asset_id:
            return {"error": "asset_id 필수"}
        asset = _asset_lib.use(asset_id)
        if not asset:
            return {"error": f"에셋 '{asset_id}' 없음"}
        return {"id": asset.id, "usage_count": asset.usage_count}

    return {"error": f"알 수 없는 action: {action}", "supported": ["save", "search", "list", "use"]}


# ── schedule ──


_scheduler = Scheduler()


@server.tool(requires="campaigns")
async def gwanjong_schedule(
    action: str,
    campaign_id: str = "",
    data: dict[str, Any] | None = None,
    state: GwanjongState | None = None,
) -> dict[str, Any]:
    """Content calendar. action: add, list, cancel, check."""
    if action == "add":
        if not data:
            return {"error": "data 필수 (campaign_id, platform, content, scheduled_at)"}
        required = ["campaign_id", "platform", "content", "scheduled_at"]
        missing = [k for k in required if k not in data]
        if missing:
            return {"error": f"필수 필드 누락: {missing}"}
        item = _scheduler.add(data)
        return {
            "id": item.id,
            "platform": item.platform,
            "scheduled_at": item.scheduled_at,
            "status": item.status,
        }

    if action == "list":
        items = _scheduler.list_all(campaign_id=campaign_id)
        return {
            "items": [
                {
                    "id": i.id,
                    "platform": i.platform,
                    "action": i.action,
                    "scheduled_at": i.scheduled_at,
                    "status": i.status,
                }
                for i in items
            ],
            "count": len(items),
        }

    if action == "cancel":
        if not data or "item_id" not in data:
            return {"error": "data.item_id 필수"}
        ok = _scheduler.cancel(data["item_id"])
        return {"cancelled": ok, "item_id": data["item_id"]}

    if action == "check":
        results = await _scheduler.process_due(bus=bus)
        return {"executed": len(results), "results": results}

    return {
        "error": f"알 수 없는 action: {action}",
        "supported": ["add", "list", "cancel", "check"],
    }


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
