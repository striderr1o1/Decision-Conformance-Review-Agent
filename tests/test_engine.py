"""ClaudeEngine's subprocess handling, JSON envelope parsing, and credential
gating — all mocked, no real `claude` invocation (see test_engine_contract.py
for the one live test)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from decision_agent.config import Config
from decision_agent.engine import (
    ClaudeEngine,
    EngineError,
    MissingCredentialError,
    extract_structured,
)
from decision_agent.schema import ReviewResult


def _fake_completed(stdout: str, returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


@pytest.fixture(autouse=True)
def _clear_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_extract_structured_with_stringified_result() -> None:
    raw = {"type": "result", "result": json.dumps({"findings": []})}
    assert extract_structured(raw) == {"findings": []}


def test_extract_structured_with_dict_result() -> None:
    raw = {"type": "result", "result": {"findings": []}}
    assert extract_structured(raw) == {"findings": []}


def test_extract_structured_without_result_key_passthrough() -> None:
    raw = {"findings": []}
    assert extract_structured(raw) == {"findings": []}


def test_extract_structured_raises_on_non_json_result_string() -> None:
    raw = {"type": "result", "result": "not schema json, just prose"}
    with pytest.raises(json.JSONDecodeError):
        extract_structured(raw)


def test_review_raises_without_credential(tmp_path: Path) -> None:
    cfg = Config(repo_root=tmp_path)
    engine = ClaudeEngine(cfg, repo_root=tmp_path)

    with pytest.raises(MissingCredentialError):
        engine.review("some prompt")


def test_review_raises_on_nonzero_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _fake_completed("", returncode=1)
    )

    cfg = Config(repo_root=tmp_path)
    engine = ClaudeEngine(cfg, repo_root=tmp_path)

    with pytest.raises(EngineError):
        engine.review("some prompt")


def test_review_raises_on_invalid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_completed("not json"))

    cfg = Config(repo_root=tmp_path)
    engine = ClaudeEngine(cfg, repo_root=tmp_path)

    with pytest.raises(EngineError):
        engine.review("some prompt")


def test_review_raises_engine_error_when_result_is_prose_not_schema_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The outer envelope is valid JSON (json.loads(result.stdout) succeeds)
    but `result` itself is prose, not schema-conforming JSON — so the
    second, inner `json.loads` inside `extract_structured` is the one that
    fails. That must still surface as EngineError, not a raw
    JSONDecodeError, or cli.py's narrow except clause lets it escape as an
    unhandled traceback."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token")
    envelope = json.dumps(
        {"type": "result", "result": "Sure, here are my thoughts on the diff: looks fine!"}
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_completed(envelope))

    cfg = Config(repo_root=tmp_path)
    engine = ClaudeEngine(cfg, repo_root=tmp_path)

    with pytest.raises(EngineError) as exc_info:
        engine.review("some prompt")

    assert "looks fine" in str(exc_info.value)


def test_review_parses_successful_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token")
    envelope = json.dumps({"type": "result", "result": json.dumps({"findings": []})})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_completed(envelope))

    cfg = Config(repo_root=tmp_path)
    engine = ClaudeEngine(cfg, repo_root=tmp_path)

    result = engine.review("some prompt")

    assert isinstance(result, ReviewResult)
    assert result.findings == []


def test_review_builds_expected_restricted_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-token")
    envelope = json.dumps({"result": json.dumps({"findings": []})})
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(envelope)

    monkeypatch.setattr(subprocess, "run", fake_run)

    cfg = Config(repo_root=tmp_path, model="sonnet")
    engine = ClaudeEngine(cfg, repo_root=tmp_path)
    engine.review("some prompt")

    cmd = captured["cmd"]
    assert cmd[0] == "claude"
    assert "--restricted" in cmd
    assert "Bash" not in cmd
    tools_index = cmd.index("--tools") + 1
    assert cmd[tools_index] == "Read,Grep,Glob"
    assert "--permission-prompts" in cmd
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
