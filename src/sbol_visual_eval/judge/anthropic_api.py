"""The Claude API judge backend.

Sends the rendered page image and the rubric-conditioned prompt through
the Anthropic SDK. Credentials resolve from the environment
(``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile).
"""

from __future__ import annotations

import base64
from typing import Any

from .parsing import parse_verdict
from .prompt import SYSTEM_PROMPT, build_user_prompt
from .rubric import RubricRule
from .schema import FigureContext, FigureVerdict

DEFAULT_MODEL = "claude-opus-5"


class AnthropicAPIJudge:
    def __init__(
        self,
        rules: list[RubricRule],
        *,
        model: str = DEFAULT_MODEL,
        max_tokens: int = 8000,
        client: Any | None = None,
    ) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self._client = client
        self._rules = rules
        self._model = model
        self._max_tokens = max_tokens

    def judge(self, context: FigureContext) -> FigureVerdict:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.standard_b64encode(context.page_png).decode(),
                            },
                        },
                        {
                            "type": "text",
                            "text": build_user_prompt(
                                context.figure_number, context.caption_text, self._rules
                            ),
                        },
                    ],
                }
            ],
        )
        reply = "".join(block.text for block in response.content if block.type == "text")
        return parse_verdict(reply, context.figure_number)
