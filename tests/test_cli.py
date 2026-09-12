"""CLI wiring for `decision-agent review`:

- `--dry-run` vs `--stub` select independent things (posting vs. engine) and
  must compose in all four combinations.
- `--explain` / `--explain-prompts` print to stderr only, never stdout, and
  cover the sections the pipeline now tracks.
- A missing credential with the real engine surfaces as a clean
  `click.ClickException`, not a traceback.

Engine-selection/posting tests mock `run_pipeline` and `decision_agent.github`
so they exercise only `cli.py`'s wiring, no subprocess, no network. The
explain-content tests run the real pipeline against a throwaway git repo
with `StubEngine`, so no LLM call is ever made.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from decision_agent import cli as cli_module
from decision_agent.config import Config
from decision_agent.gitctx import DiffContext
from decision_agent.review import PipelineResult

HTTP_DECISION = """\
---
id: "0007"
title: All outbound HTTP goes through the shared client
status: accepted
date: 2026-03-14
scope:
  - "src/**/*.py"
---

## Decision
All outbound HTTP MUST go through `src/http/client.py`.
"""

SUPERSEDED_DECISION = """\
---
id: "0001"
title: Old HTTP rule
status: superseded
date: 2025-01-01
scope: ["src/**/*.py"]
---

## Decision
Old rule, no longer in force.
"""


def _empty_pipeline_result(repo_root: Path) -> PipelineResult:
    ctx = DiffContext(base="main", head="HEAD", merge_base="deadbeef", diff="", changed_files=[])
    return PipelineResult(
        diff_context=ctx,
        all_decisions=[],
        selected_decisions=[],
        findings=[],
        proposals=[],
    )


@pytest.fixture
def _wired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Patch `load_config`, `run_pipeline`, the two engine classes, and
    every `decision_agent.github` entry point the CLI touches, so a test
    can assert purely on cli.py's wiring: which engine class got
    constructed, and whether GitHub was contacted at all."""
    monkeypatch.setattr(cli_module, "load_config", lambda repo_root, path=None: Config(repo_root=repo_root))

    captured: dict = {}

    def fake_run_pipeline(cfg, engine, root, base=None, head=None):
        captured["engine"] = engine
        return _empty_pipeline_result(root)

    monkeypatch.setattr(cli_module, "run_pipeline", fake_run_pipeline)

    claude_engine_instance = MagicMock(name="ClaudeEngine-instance")
    stub_engine_instance = MagicMock(name="StubEngine-instance")
    monkeypatch.setattr(cli_module, "ClaudeEngine", MagicMock(return_value=claude_engine_instance))
    monkeypatch.setattr(cli_module, "StubEngine", MagicMock(return_value=stub_engine_instance))

    github = SimpleNamespace(
        resolve_pr_number=MagicMock(return_value=42),
        remove_comment=MagicMock(),
        remove_label=MagicMock(),
        upsert_comment=MagicMock(),
        ensure_label=MagicMock(),
    )
    for name, mock in vars(github).items():
        monkeypatch.setattr(f"decision_agent.github.{name}", mock)

    return SimpleNamespace(
        captured=captured,
        github=github,
        claude_engine_instance=claude_engine_instance,
        stub_engine_instance=stub_engine_instance,
    )


def _invoke(tmp_path: Path, *extra_args: str):
    return CliRunner().invoke(cli_module.main, ["review", "--repo-root", str(tmp_path), *extra_args])


def test_no_flags_uses_real_engine_and_posts_to_github(_wired, tmp_path: Path) -> None:
    result = _invoke(tmp_path)
    assert result.exit_code == 0, result.output
    assert _wired.captured["engine"] is _wired.claude_engine_instance
    _wired.github.resolve_pr_number.assert_called_once()
    # no findings/proposals -> comment_body is None -> the "clean PR" branch
    _wired.github.remove_comment.assert_called_once()
    _wired.github.upsert_comment.assert_not_called()


def test_dry_run_alone_uses_real_engine_and_never_touches_github(_wired, tmp_path: Path) -> None:
    result = _invoke(tmp_path, "--dry-run")
    assert result.exit_code == 0, result.output
    assert _wired.captured["engine"] is _wired.claude_engine_instance
    _wired.github.resolve_pr_number.assert_not_called()
    _wired.github.remove_comment.assert_not_called()
    assert "no comment" in result.stdout


def test_stub_alone_uses_stub_engine_and_still_posts_to_github(_wired, tmp_path: Path) -> None:
    """`--stub` says nothing about posting -- on its own it should post the
    (empty) stub result, which is the point: testing the posting path
    without spending tokens."""
    result = _invoke(tmp_path, "--stub")
    assert result.exit_code == 0, result.output
    assert _wired.captured["engine"] is _wired.stub_engine_instance
    _wired.github.resolve_pr_number.assert_called_once()


def test_dry_run_and_stub_together_reproduces_old_dry_run_behavior(_wired, tmp_path: Path) -> None:
    result = _invoke(tmp_path, "--dry-run", "--stub")
    assert result.exit_code == 0, result.output
    assert _wired.captured["engine"] is _wired.stub_engine_instance
    _wired.github.resolve_pr_number.assert_not_called()
    assert "no comment" in result.stdout


def test_help_documents_dry_run_and_stub_as_independent() -> None:
    result = CliRunner().invoke(cli_module.main, ["review", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output
    assert "--stub" in result.output
    assert "--explain" in result.output
    # The no-LLM/no-credential property belongs to --stub, not --dry-run.
    assert "credential" in result.output


# --- explain: real pipeline + StubEngine, no mocks, no LLM calls ----------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout


@pytest.fixture
def decision_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")

    decisions_dir = tmp_path / "decisions"
    decisions_dir.mkdir()
    (decisions_dir / "0007-http.md").write_text(HTTP_DECISION)
    (decisions_dir / "0001-old.md").write_text(SUPERSEDED_DECISION)
    (tmp_path / "README.md").write_text("hello\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "initial")

    return tmp_path


def test_explain_goes_to_stderr_and_comment_body_stays_on_stdout(decision_repo: Path) -> None:
    _git(decision_repo, "checkout", "-q", "-b", "feature")
    src = decision_repo / "src"
    src.mkdir()
    (src / "service.py").write_text("import requests\nrequests.get('http://x')\n")
    _git(decision_repo, "add", "-A")
    _git(decision_repo, "commit", "-q", "-m", "direct requests call")

    result = CliRunner().invoke(
        cli_module.main,
        [
            "review",
            "--repo-root",
            str(decision_repo),
            "--base",
            "main",
            "--head",
            "feature",
            "--dry-run",
            "--stub",
            "--explain",
        ],
    )

    assert result.exit_code == 0, result.output

    for header in (
        "1. COLLECT",
        "2. IGNORE FILTER",
        "3. SELECT",
        "4. REVIEW",
        "5. VERIFY",
        "6. PROPOSE",
        "7. THRESHOLD",
    ):
        assert header in result.stderr, result.stderr
        assert header not in result.stdout

    # stdout is just the (pipeable) comment body / clean-PR message.
    assert "no comment" in result.stdout
    assert "COLLECT" not in result.stdout


def test_explain_reports_superseded_decision_reason(decision_repo: Path) -> None:
    _git(decision_repo, "checkout", "-q", "-b", "feature")
    src = decision_repo / "src"
    src.mkdir()
    (src / "service.py").write_text("import requests\n")
    _git(decision_repo, "add", "-A")
    _git(decision_repo, "commit", "-q", "-m", "touch src")

    result = CliRunner().invoke(
        cli_module.main,
        ["review", "--repo-root", str(decision_repo), "--base", "main", "--head", "feature", "--stub", "--explain", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "0001" in result.stderr
    assert "not active" in result.stderr
    assert "superseded" in result.stderr


def test_explain_reports_scope_miss_reason(decision_repo: Path) -> None:
    _git(decision_repo, "checkout", "-q", "-b", "feature")
    (decision_repo / "README.md").write_text("updated, no src touched\n")
    _git(decision_repo, "add", "-A")
    _git(decision_repo, "commit", "-q", "-m", "readme only")

    result = CliRunner().invoke(
        cli_module.main,
        ["review", "--repo-root", str(decision_repo), "--base", "main", "--head", "feature", "--stub", "--explain", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "0007" in result.stderr
    assert "no scope glob matched any changed file" in result.stderr


def test_explain_prompts_dumps_full_prompt_text(decision_repo: Path) -> None:
    _git(decision_repo, "checkout", "-q", "-b", "feature")
    src = decision_repo / "src"
    src.mkdir()
    (src / "service.py").write_text("import requests\nrequests.get('http://x')\n")
    _git(decision_repo, "add", "-A")
    _git(decision_repo, "commit", "-q", "-m", "direct requests call")

    result = CliRunner().invoke(
        cli_module.main,
        [
            "review",
            "--repo-root",
            str(decision_repo),
            "--base",
            "main",
            "--head",
            "feature",
            "--stub",
            "--explain-prompts",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "PROMPTS" in result.stderr
    assert "## Recorded decisions" in result.stderr
    assert "import requests" in result.stderr
    # stub engine finds nothing, so verify is never invoked
    assert "(not sent)" in result.stderr


def test_explain_without_prompts_flag_omits_prompt_text(decision_repo: Path) -> None:
    _git(decision_repo, "checkout", "-q", "-b", "feature")
    src = decision_repo / "src"
    src.mkdir()
    (src / "service.py").write_text("import requests\nrequests.get('http://x')\n")
    _git(decision_repo, "add", "-A")
    _git(decision_repo, "commit", "-q", "-m", "direct requests call")

    result = CliRunner().invoke(
        cli_module.main,
        ["review", "--repo-root", str(decision_repo), "--base", "main", "--head", "feature", "--stub", "--explain", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "PROMPTS" not in result.stderr
    assert "## Recorded decisions" not in result.stderr


def test_dry_run_without_stub_surfaces_missing_credential_cleanly(
    decision_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real engine + --dry-run + no credential must raise a clean
    click.ClickException (exit code 1, `Error: ...` message), never an
    uncaught traceback -- MissingCredentialError is a subclass of
    EngineError, and cli.py already catches EngineError around
    run_pipeline, so this should just work end to end."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    _git(decision_repo, "checkout", "-q", "-b", "feature")
    src = decision_repo / "src"
    src.mkdir()
    (src / "service.py").write_text("import requests\nrequests.get('http://x')\n")
    _git(decision_repo, "add", "-A")
    _git(decision_repo, "commit", "-q", "-m", "direct requests call")

    result = CliRunner().invoke(
        cli_module.main,
        ["review", "--repo-root", str(decision_repo), "--base", "main", "--head", "feature", "--dry-run"],
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "credential" in result.output
    assert isinstance(result.exception, SystemExit)
