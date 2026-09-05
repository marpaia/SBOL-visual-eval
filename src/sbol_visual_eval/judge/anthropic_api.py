"""The Claude API judge backend.

Sends the rendered page image and the rubric-conditioned prompt through
the Anthropic SDK. Credentials resolve from the environment
(``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile).
"""

from __future__ import annotations

import base64
from typing import Any

from .parsing import parse_verdict
from .prompt import (
    SYSTEM_PROMPT,
    build_compatibility_prompt,
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

    def _prompt(self, context: FigureContext) -> str:
        if self._compatibility_only:
            return build_compatibility_prompt(context.figure_number, context.caption_text)
        return build_user_prompt(context.figure_number, context.caption_text, self._rules)

    def judge(self, context: FigureContext) -> FigureVerdict:
        content = []
        for exemplar in self._exemplars:
            content.extend(
                [
                    self._image_block(exemplar.page_png),
                    {"type": "text", "text": render_compatibility_exemplar(exemplar)},
                ]
            )
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
