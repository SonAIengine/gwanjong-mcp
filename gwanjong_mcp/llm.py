"""내장 LLM 클라이언트 — 자율 모드에서 댓글 생성용. 독립 모듈."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Any

from .persona import Persona, PersonaManager
from .types import DraftContext

logger = logging.getLogger(__name__)

# pipeline.py의 writing guide를 재사용
from .pipeline import _build_writing_guide, WRITING_AVOID


class CommentGenerator:
    """댓글 생성 전용 LLM 클라이언트.

    백엔드 우선순위:
    1. claude CLI (Claude Code) — API 키 불필요, 로컬 인증 사용
    2. anthropic SDK — ANTHROPIC_API_KEY 환경변수 필요
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
        """사용 가능한 백엔드 자동 감지."""
        if self._backend != "auto":
            return self._backend
        # claude CLI 존재 여부 확인
        if shutil.which("claude"):
            return "cli"
        # anthropic SDK + API key
        if os.getenv("ANTHROPIC_API_KEY"):
            return "sdk"
        raise RuntimeError(
            "LLM 백엔드 없음. claude CLI를 설치하거나 ANTHROPIC_API_KEY를 설정하세요."
        )

    async def generate(
        self,
        context: DraftContext,
        max_tokens: int = 300,
    ) -> str:
        """DraftContext + persona 기반으로 댓글 생성."""
        backend = self._resolve_backend()
        if backend == "cli":
            return await self._generate_cli(context, max_tokens)
        return await self._generate_sdk(context, max_tokens)

    # ── Claude CLI 백엔드 ──

    async def _generate_cli(self, ctx: DraftContext, max_tokens: int) -> str:
        """claude -p 로 댓글 생성. API 키 불필요."""
        persona = self.persona.get(ctx.platform)
        system_prompt = self._build_system_prompt(ctx, persona)
        user_prompt = self._build_user_prompt(ctx)
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

        cmd = [
            "claude", "-p", full_prompt,
            "--model", self.model,
            "--max-turns", "1",
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
            raise RuntimeError(f"claude CLI 에러 (code {proc.returncode}): {err}")

        content = stdout.decode().strip()
        logger.info(
            "Generated comment (CLI): %d chars, model=%s, platform=%s",
            len(content), self.model, ctx.platform,
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
        """anthropic SDK로 댓글 생성."""
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
            len(content), self.model, ctx.platform,
        )
        return content

    # ── 프롬프트 빌더 ──

    def _build_system_prompt(
        self, ctx: DraftContext, persona: Persona, action: str = "comment",
    ) -> str:
        """시스템 프롬프트 조합."""
        from .types import Opportunity
        fake_opp = Opportunity(
            id=ctx.opportunity_id, platform=ctx.platform,
            post_id="", title=ctx.title, url="",
            relevance=0, comments_count=0, reason="",
        )
        writing_guide = _build_writing_guide(fake_opp, ctx.tone, action=action)
        avoid = "\n".join(f"- {a}" for a in WRITING_AVOID)
        output_label = "post" if action == "post" else "comment"

        return f"""{writing_guide}

PERSONA:
{persona.to_system_prompt()}

AVOID:
{avoid}

Output ONLY the {output_label} text. No quotes, no labels, no explanation."""

    def _build_user_prompt(self, ctx: DraftContext, action: str = "comment") -> str:
        """유저 프롬프트 조합."""
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
