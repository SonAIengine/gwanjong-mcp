"""gwanjong-daemon CLI 진입점 — 자율 모드 실행."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

from .autonomous import AutonomousLoop, CycleConfig
from .events import EventBus
from .llm import CommentGenerator
from .memory import Memory
from .safety import Safety

# ~/.gwanjong/.env 로드
_env_path = Path.home() / ".gwanjong" / ".env"
if _env_path.exists():
    load_dotenv(_env_path)


def main() -> None:
    """CLI 진입점."""
    parser = argparse.ArgumentParser(
        prog="gwanjong-daemon",
        description="gwanjong 자율 소셜 에이전트 데몬",
    )
    parser.add_argument(
        "--topics", "-t",
        type=str,
        default="MCP",
        help="쉼표로 구분된 토픽 목록 (기본: MCP)",
    )
    parser.add_argument(
        "--interval", "-i",
        type=float,
        default=4.0,
        help="사이클 간격 (시간, 기본: 4.0)",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=3,
        help="사이클당 최대 액션 수 (기본: 3)",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="최대 사이클 수 (기본: 무한)",
    )
    parser.add_argument(
        "--platforms",
        type=str,
        default=None,
        help="쉼표로 구분된 플랫폼 (기본: 전체)",
    )
    parser.add_argument(
        "--require-approval",
        action="store_true",
        help="strike 전 승인 필요",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="claude-haiku-4-5-20251001",
        help="LLM 모델 (기본: claude-haiku-4-5-20251001)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scout + draft만 실행, strike 안 함",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="디버그 로그 출력",
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
        require_approval=args.require_approval or args.dry_run,
    )

    # EventBus + 플러그인 조립
    bus = EventBus()
    Safety().attach(bus)
    Memory().attach(bus)

    llm = CommentGenerator(model=args.model)

    loop = AutonomousLoop(bus=bus, llm=llm, config=config)

    asyncio.run(
        loop.run_daemon(
            interval_hours=args.interval,
            max_cycles=args.max_cycles,
        )
    )


if __name__ == "__main__":
    main()
