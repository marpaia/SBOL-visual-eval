"""The Claude API judge backend.

Sends the rendered page image and the rubric-conditioned prompt through
the Anthropic SDK. Credentials resolve from the environment
(``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile).
"""

from __future__ import annotations

import base64
from typing import Any

from .parsing import parse_verdict, parse_verdicts
from .prompt import (
    PAPER_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_compatibility_paper_prompt,
    build_compatibility_prompt,
    build_paper_user_prompt,
    build_user_prompt,
    render_compatibility_exemplar,
)
from .rubric import RubricRule
from .schema import CompatibilityExemplar, FigureContext, FigureVerdict

DEFAULT_MODEL = "claude-opus-5"


class AnthropicAPIJudge:
    def __init__(
        self,
        rules: list[RubricRule],
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 8000,
        client: Any | None = None,
        compatibility_only: bool = False,
        exemplars: tuple[CompatibilityExemplar, ...] = (),
        era_conditioned: bool = False,
        request_borderline: bool = False,
        assume_compatible: bool = False,
    ) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self._client = client
        self._rules = rules
        self._model = model
        self._max_tokens = max_tokens
        self._compatibility_only = compatibility_only
        self._exemplars = exemplars
        self._era_conditioned = era_conditioned
        self._request_borderline = request_borderline
        self._assume_compatible = assume_compatible

    def metadata(self) -> dict[str, str]:
        """Identify the provider and pinned model used in benchmark reports."""
        return {"backend": "anthropic", "model": self._model}

    def _prompt(self, context: FigureContext) -> str:
        if self._compatibility_only:
            return build_compatibility_prompt(
                context.figure_number,
                context.caption_text,
                publication_year=context.publication_year,
                era_conditioned=self._era_conditioned,
                request_borderline=self._request_borderline,
            )
        return build_user_prompt(
            context.figure_number,
            context.caption_text,
            self._rules,
            publication_year=context.publication_year,
            era_conditioned=self._era_conditioned,
            request_borderline=self._request_borderline,
            assume_compatible=self._assume_compatible,
        )

    def _paper_prompt(self, contexts: tuple[FigureContext, ...]) -> str:
        if self._compatibility_only:
            return build_compatibility_paper_prompt(
                contexts,
                era_conditioned=self._era_conditioned,
                request_borderline=self._request_borderline,
            )
        return build_paper_user_prompt(
            contexts,
            self._rules,
            era_conditioned=self._era_conditioned,
            request_borderline=self._request_borderline,
            assume_compatible=self._assume_compatible,
        )

    def judge(self, context: FigureContext) -> FigureVerdict:
        content = self._exemplar_content()
        content.extend(
            [
                self._image_block(context.page_png),
                {"type": "text", "text": self._prompt(context)},
            ]
        )
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
        )
        reply = "".join(block.text for block in response.content if block.type == "text")
        return parse_verdict(reply, context.figure_number)

    def judge_paper(self, contexts: tuple[FigureContext, ...]) -> tuple[FigureVerdict, ...]:
        if not contexts:
            return ()
        content = self._exemplar_content()
        page_groups: dict[int | str, list[FigureContext]] = {}
        for context in contexts:
            page_key: int | str = (
                context.page_number
                if context.page_number is not None
                else f"figure-{context.figure_number}"
            )
            page_groups.setdefault(page_key, []).append(context)
        for page_contexts in page_groups.values():
            figures = ", ".join(str(context.figure_number) for context in page_contexts)
            content.extend(
                [
                    self._image_block(page_contexts[0].page_png),
                    {"type": "text", "text": f"Target page containing Figure(s) {figures}."},
                ]
            )
        content.append({"type": "text", "text": self._paper_prompt(contexts)})
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max(self._max_tokens, 32000),
            system=PAPER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        reply = "".join(block.text for block in response.content if block.type == "text")
        return parse_verdicts(reply, tuple(context.figure_number for context in contexts))

    def _exemplar_content(self) -> list[dict[str, Any]]:
        content = []
        for exemplar in self._exemplars:
            content.extend(
                [
                    self._image_block(exemplar.page_png),
                    {"type": "text", "text": render_compatibility_exemplar(exemplar)},
                ]
            )
        return content

    @staticmethod
    def _image_block(page_png: bytes) -> dict[str, Any]:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(page_png).decode(),
            },
        }
