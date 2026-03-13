"""승인 대기열 CLI."""

from __future__ import annotations

import argparse
import asyncio
import json

from .approval import ApprovalQueue


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gwanjong-approval",
        description="gwanjong approval queue manager",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="pending 승인 대기열 조회")
    list_parser.add_argument("--platform", default=None, help="플랫폼 필터")

    show_parser = subparsers.add_parser("show", help="단건 상세 조회")
    show_parser.add_argument("item_id", type=int, help="approval item id")

    approve_parser = subparsers.add_parser("approve", help="항목 승인 처리")
    approve_parser.add_argument("item_id", type=int, help="approval item id")

    retry_parser = subparsers.add_parser("retry", help="실패한 항목 재시도")
    retry_parser.add_argument("item_id", type=int, help="approval item id")

    reject_parser = subparsers.add_parser("reject", help="항목 거절 처리")
    reject_parser.add_argument("item_id", type=int, help="approval item id")

    subparsers.add_parser("stats", help="승인 큐 통계 조회")
    return parser


def _print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    queue = ApprovalQueue()

    if args.command == "list":
        items = queue.get_pending(platform=args.platform)
        summary = [
            {
                "id": item["id"],
                "topic": item["topic"],
                "platform": item["platform"],
                "action": item["action"],
                "title": item["title"],
                "created_at": item["created_at"],
            }
            for item in items
        ]
        _print_json(summary)
        return

    if args.command == "show":
        item = queue.get_item(args.item_id)
        if item is None:
            raise SystemExit(f"approval item not found: {args.item_id}")
        _print_json(item)
        return

    if args.command == "approve":
        item = queue.get_item(args.item_id)
        if item is None:
            raise SystemExit(f"approval item not found: {args.item_id}")
        result = asyncio.run(queue.execute_approved(args.item_id))
        _print_json(result)
        return

    if args.command == "retry":
        item = queue.get_item(args.item_id)
        if item is None:
            raise SystemExit(f"approval item not found: {args.item_id}")
        result = asyncio.run(queue.retry_failed(args.item_id))
        _print_json(result)
        return

    if args.command == "reject":
        item = queue.get_item(args.item_id)
        if item is None:
            raise SystemExit(f"approval item not found: {args.item_id}")
        queue.mark_rejected(args.item_id)
        _print_json({"id": args.item_id, "status": "rejected"})
        return

    if args.command == "stats":
        _print_json(queue.stats())
        return


if __name__ == "__main__":
    main()
