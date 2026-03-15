"""Shared policy definitions for platform activity limits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformLimit:
    """Per-platform activity limits."""

    platform: str
    max_comments_per_day: int = 3
    max_posts_per_day: int = 1
    max_upvotes_per_day: int = 5
    min_interval_minutes: int = 30
    cooldown_after_error_minutes: int = 60


DEFAULT_LIMITS: dict[str, PlatformLimit] = {
    "devto": PlatformLimit(
        "devto", max_comments_per_day=5, max_posts_per_day=1, min_interval_minutes=5
    ),
    "bluesky": PlatformLimit(
        "bluesky", max_comments_per_day=8, max_posts_per_day=2, min_interval_minutes=5
    ),
    "twitter": PlatformLimit(
        "twitter", max_comments_per_day=8, max_posts_per_day=2, min_interval_minutes=5
    ),
    "reddit": PlatformLimit(
        "reddit", max_comments_per_day=5, max_posts_per_day=0, min_interval_minutes=5
    ),
    "github_discussions": PlatformLimit(
        "github_discussions",
        max_comments_per_day=6,
        max_posts_per_day=1,
        max_upvotes_per_day=10,
        min_interval_minutes=5,
    ),
    "discourse": PlatformLimit(
        "discourse",
        max_comments_per_day=6,
        max_posts_per_day=1,
        max_upvotes_per_day=10,
        min_interval_minutes=5,
    ),
}

PLATFORMS = list(DEFAULT_LIMITS)
