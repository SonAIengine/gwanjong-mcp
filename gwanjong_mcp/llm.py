"""Built-in LLM client for comment generation in autonomous mode. Standalone module."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Any

from .persona import Persona, PersonaManager
from .pipeline import WRITING_AVOID, _build_writing_guide
from .types import DraftContext

logger = logging.getLogger(__name__)


class CommentGenerator:
    """LLM client dedicated to comment generation.

    Backend priority:
    1. claude CLI (Claude Code) — no API key needed, uses local auth
    2. anthropic SDK — requires ANTHROPIC_API_KEY environment variable
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        persona_manager: PersonaManager | None = None,
        backend: str = "auto",
    ) -> None:
        self.model = model
        self.persona = persona_manager or PersonaManager()
        self._backend = backend  # "auto", "cli", "sdk"
        self._client: Any = None

    def _resolve_backend(self) -> str:
        """Auto-detect available backend."""
        if self._backend != "auto":
            return self._backend
        # claude CLI 존재 여부 확인
        if shutil.which("claude"):
            return "cli"
        # anthropic SDK + API key
        if os.getenv("ANTHROPIC_API_KEY"):
            return "sdk"
        raise RuntimeError("No LLM backend available. Install claude CLI or set ANTHROPIC_API_KEY.")

    async def generate(
        self,
        context: DraftContext,
        max_tokens: int = 300,
    ) -> str:
        """Generate a comment based on DraftContext and persona."""
        backend = self._resolve_backend()
        if backend == "cli":
            return await self._generate_cli(context, max_tokens)
        return await self._generate_sdk(context, max_tokens)

    # ── Claude CLI 백엔드 ──

    async def _generate_cli(self, ctx: DraftContext, max_tokens: int) -> str:
        """Generate a comment via claude -p. No API key required."""
        persona = self.persona.get(ctx.platform)
        system_prompt = self._build_system_prompt(ctx, persona)
        user_prompt = self._build_user_prompt(ctx)
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

        cmd = [
            "claude",
            "-p",
            full_prompt,
            "--model",
            self.model,
            "--max-turns",
            "1",
        ]

        # Claude Code 중첩 세션 차단 우회
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode().strip()
            raise RuntimeError(f"claude CLI error (code {proc.returncode}): {err}")

        content = stdout.decode().strip()
        logger.info(
            "Generated comment (CLI): %d chars, model=%s, platform=%s",
            len(content),
            self.model,
            ctx.platform,
        )
        return content

    # ── Anthropic SDK 백엔드 ──

    def _get_client(self) -> Any:
        """anthropic AsyncClient lazy init."""
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError("pip install anthropic")
            self._client = anthropic.AsyncAnthropic(
                api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            )
        return self._client

    async def _generate_sdk(self, ctx: DraftContext, max_tokens: int) -> str:
        """Generate a comment via the anthropic SDK."""
        persona = self.persona.get(ctx.platform)
        system_prompt = self._build_system_prompt(ctx, persona)
        user_prompt = self._build_user_prompt(ctx)

        client = self._get_client()
        response = await client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        content = response.content[0].text.strip()
        logger.info(
            "Generated comment (SDK): %d chars, model=%s, platform=%s",
            len(content),
            self.model,
            ctx.platform,
        )
        return content

    # ── 프롬프트 빌더 ──

    def _build_system_prompt(
        self,
        ctx: DraftContext,
        persona: Persona,
        action: str = "comment",
    ) -> str:
        """Build the system prompt."""
        from .types import Opportunity

        fake_opp = Opportunity(
            id=ctx.opportunity_id,
            platform=ctx.platform,
            post_id="",
            title=ctx.title,
            url="",
            relevance=0,
            comments_count=0,
            reason="",
        )
        writing_guide = _build_writing_guide(fake_opp, ctx.tone, action=action)
        avoid = "\n".join(f"- {a}" for a in WRITING_AVOID)
        output_label = "post" if action == "post" else "comment"

        # 에이전트 캐릭터 personality (환경변수로 전달됨)
        agent_name = os.getenv("GWANJONG_AGENT_NAME", "")
        agent_personality = os.getenv("GWANJONG_AGENT_PERSONALITY", "")
        agent_section = ""
        if agent_personality:
            agent_section = f"\nCHARACTER:\nYour name is {agent_name}. {agent_personality}\nWrite in a way that reflects this personality.\n"

        return f"""{writing_guide}

PERSONA:
{persona.to_system_prompt()}
{agent_section}
AVOID:
{avoid}

LANGUAGE:
Detect the language of the post title and body. Write your {output_label} in the SAME language.
If the post is in English, write in English. If in Korean, write in Korean. Never mix languages unless the post does.

Output ONLY the {output_label} text. No quotes, no labels, no explanation."""

    def _build_user_prompt(self, ctx: DraftContext, action: str = "comment") -> str:
        """Build the user prompt."""
        parts = [
            f"Post title: {ctx.title}",
            f"Post body (excerpt): {ctx.body_summary[:300]}",
        ]
        if ctx.top_comments:
            parts.append("Existing comments:")
            for i, c in enumerate(ctx.top_comments[:3], 1):
                parts.append(f"  {i}. {c[:150]}")
        parts.append(f"\nTone of discussion: {ctx.tone}")
        parts.append(f"Suggested approach: {ctx.suggested_approach}")
        if action == "post":
            parts.append("\nWrite a post:")
        else:
            parts.append("\nWrite a comment:")
        return "\n".join(parts)
