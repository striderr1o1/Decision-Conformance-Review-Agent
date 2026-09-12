"""One test that actually invokes `claude -p`, to catch CLI flag drift.

Marked `live` and excluded from the default test run (see pyproject.toml's
`addopts = "-m 'not live'"`). Run explicitly with:

    pytest -m live tests/test_engine_contract.py

Requires CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in the environment
and the `claude` CLI on PATH.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decision_agent.config import Config
from decision_agent.engine import ClaudeEngine
from decision_agent.schema import ReviewResult


@pytest.mark.live
def test_claude_cli_produces_schema_conformant_output(tmp_path: Path) -> None:
    cfg = Config(repo_root=tmp_path, model="sonnet")
    engine = ClaudeEngine(cfg, repo_root=tmp_path)

    prompt = (
        "## Recorded decisions\n\n(no decisions apply)\n\n"
        "## Pull request diff\n\n```diff\ndiff --git a/x.txt b/x.txt\n"
        "+hello world\n```\n"
    )

    result = engine.review(prompt)

    assert isinstance(result, ReviewResult)
