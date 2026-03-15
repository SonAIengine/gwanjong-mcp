"""gwanjong-daemon CLI entry point — autonomous mode execution."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from .autonomous import AutonomousLoop, CycleConfig
from .events import EventBus
from .llm import CommentGenerator
from .memory import Memory
from .safety import Safety

logger = logging.getLogger(__name__)

# Load .env (configurable via GWANJONG_ENV_PATH)
_env_path = Path(os.getenv("GWANJONG_ENV_PATH", str(Path.home() / ".gwanjong" / ".env")))
if _env_path.exists():
    load_dotenv(_env_path)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="gwanjong-daemon",
        description="gwanjong autonomous social agent daemon",
    )
    parser.add_argument(
        "--topics",
        "-t",
        type=str,
        default="MCP",
        help="comma-separated topics (default: MCP)",
    )
    parser.add_argument(
        "--interval",
        "-i",
        type=float,
        default=4.0,
        help="cycle interval in hours (default: 4.0)",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=3,
        help="max actions per cycle (default: 3)",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="max cycles (default: unlimited)",
    )
    parser.add_argument(
        "--platforms",
        type=str,
        default=None,
        help="comma-separated platforms (default: all configured)",
    )
    parser.add_argument(
        "--require-approval",
        action="store_true",
        help="require approval before strike",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="claude-haiku-4-5-20251001",
        help="LLM model (default: claude-haiku-4-5-20251001)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scout + draft only, skip strike",
    )
    parser.add_argument(
        "--campaign",
        type=str,
        default=None,
        help="campaign ID to operate under",
    )
    parser.add_argument(
        "--auto-plan",
        action="store_true",
        help="auto-generate weekly plan on first cycle",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable debug logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )

    topics = [t.strip() for t in args.topics.split(",")]
    platforms = [p.strip() for p in args.platforms.split(",")] if args.platforms else None

    config = CycleConfig(
        topics=topics,
        max_actions_per_cycle=args.max_actions,
        platforms=platforms,
        require_approval=args.require_approval,
        dry_run=args.dry_run,
        campaign_id=args.campaign or "",
    )

    # EventBus + 플러그인 조립
    bus = EventBus()
    Safety().attach(bus)
    Memory().attach(bus)

    from .conversion import ConversionTracker

    ConversionTracker().attach(bus)

    llm = CommentGenerator(model=args.model)

    loop = AutonomousLoop(bus=bus, llm=llm, config=config)

    # auto-plan 처리
    if args.auto_plan and args.campaign:
        from .strategy import StrategyEngine

        async def _run_with_plan() -> None:
            strategy = StrategyEngine(llm=llm)
            plan = await strategy.generate_weekly_plan(args.campaign)
            if "error" not in plan:
                strategy.auto_approve_low_risk(plan)
                logger.info("Auto-plan generated for %s", args.campaign)
            await loop.run_daemon(
                interval_hours=args.interval,
                max_cycles=args.max_cycles,
            )

        asyncio.run(_run_with_plan())
    else:
        asyncio.run(
            loop.run_daemon(
                interval_hours=args.interval,
                max_cycles=args.max_cycles,
            )
        )


if __name__ == "__main__":
    main()
