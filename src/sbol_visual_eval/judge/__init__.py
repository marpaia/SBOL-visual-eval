"""Rubric-conditioned per-figure judgment.

- :mod:`.rubric` loads the historical 31-rule checklist the judge applies.
- :mod:`.prompt` builds the compatibility-plus-rules judging prompt.
- :mod:`.schema` defines verdicts; compliant and best-practice outcomes are
  derived from per-rule findings under the historical cascade.
- :mod:`.parsing` turns model replies into verdicts, loudly on failure.
- :mod:`.protocol` is the backend interface; :mod:`.anthropic_api` judges
  through the Claude API, :mod:`.claude_cli` through a headless authenticated
  Claude Code CLI, and :mod:`.codex_cli` through an authenticated Codex CLI.
"""

from .anthropic_api import AnthropicAPIJudge
from .claude_cli import ClaudeCLIJudge
from .codex_cli import CodexCLIJudge
from .parsing import JudgeParseError, parse_verdict
from .protocol import FigureJudge
from .rubric import RubricRule, best_practice_rules, compliance_rules, load_rubric, render_rubric
from .schema import FigureContext, FigureVerdict, RuleFinding, RuleVerdict

__all__ = [
    "AnthropicAPIJudge",
    "ClaudeCLIJudge",
    "CodexCLIJudge",
    "FigureContext",
    "FigureJudge",
    "FigureVerdict",
    "JudgeParseError",
    "RubricRule",
    "RuleFinding",
    "RuleVerdict",
    "best_practice_rules",
    "compliance_rules",
    "load_rubric",
    "parse_verdict",
    "render_rubric",
]
