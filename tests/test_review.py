"""Stage orchestration: chunking, prompt building, and the full pipeline
against a fake engine (no subprocess, no network, no tokens)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from decision_agent.config import Config
from decision_agent.review import (
    build_review_prompt,
    chunk_diff,
    run_pipeline,
)
from decision_agent.schema import (
    Finding,
    ProposedDecision,
    ProposeResult,
    ReviewResult,
    VerifiedFinding,
    VerifyResult,
)

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


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout


@pytest.fixture
def repo_with_decision(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")

    decisions_dir = tmp_path / "decisions"
    decisions_dir.mkdir()
    (decisions_dir / "0007-http.md").write_text(HTTP_DECISION)
    (tmp_path / "README.md").write_text("hello\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "initial")

    return tmp_path


class FakeEngine:
    """Records prompts it was called with and returns canned results."""

    def __init__(self, review_result=None, verify_result=None, propose_result=None):
        self.review_result = review_result or ReviewResult(findings=[])
        self.verify_result = verify_result or VerifyResult(verified=[])
        self.propose_result = propose_result or ProposeResult(proposals=[])
        self.review_calls: list[str] = []
        self.verify_calls: list[str] = []
        self.propose_calls: list[str] = []

    def review(self, prompt: str) -> ReviewResult:
        self.review_calls.append(prompt)
        return self.review_result

    def verify(self, prompt: str) -> VerifyResult:
        self.verify_calls.append(prompt)
        return self.verify_result

    def propose(self, prompt: str) -> ProposeResult:
        self.propose_calls.append(prompt)
        return self.propose_result


def test_chunk_diff_returns_single_chunk_when_small() -> None:
    diff = "diff --git a/a.py b/a.py\n+hello\n"
    assert chunk_diff(diff, max_bytes=10_000) == [diff]


def test_chunk_diff_splits_on_file_boundaries_when_large() -> None:
    file_a = "diff --git a/a.py b/a.py\n" + ("x" * 50) + "\n"
    file_b = "diff --git a/b.py b/b.py\n" + ("y" * 50) + "\n"
    diff = file_a + file_b

    chunks = chunk_diff(diff, max_bytes=60)

    assert len(chunks) == 2
    assert "a.py" in chunks[0]
    assert "b.py" in chunks[1]
    assert "".join(chunks) == diff


def test_build_review_prompt_includes_decision_and_diff(tmp_path: Path) -> None:
    from decision_agent.decisions import parse_decision_file

    path = tmp_path / "0007-http.md"
    path.write_text(HTTP_DECISION)
    decision = parse_decision_file(path)

    prompt = build_review_prompt([decision], "diff --git a/x.py b/x.py\n+import requests\n")

    assert "0007" in prompt
    assert "shared client" in prompt
    assert "import requests" in prompt


def test_pipeline_skips_engine_when_no_decision_in_scope(repo_with_decision: Path) -> None:
    _git(repo_with_decision, "checkout", "-q", "-b", "feature")
    (repo_with_decision / "README.md").write_text("hello world, updated\n")
    _git(repo_with_decision, "add", "-A")
    _git(repo_with_decision, "commit", "-q", "-m", "touch readme only")

    cfg = Config(repo_root=repo_with_decision, propose_decisions=False)
    engine = FakeEngine()

    result = run_pipeline(cfg, engine, repo_with_decision, base="main", head="feature")

    assert result.selected_decisions == []
    assert result.findings == []
    assert engine.review_calls == []
    assert engine.verify_calls == []


def test_pipeline_runs_review_and_verify_when_in_scope(repo_with_decision: Path) -> None:
    _git(repo_with_decision, "checkout", "-q", "-b", "feature")
    src_dir = repo_with_decision / "src"
    src_dir.mkdir()
    (src_dir / "service.py").write_text("import requests\nrequests.get('http://x')\n")
    _git(repo_with_decision, "add", "-A")
    _git(repo_with_decision, "commit", "-q", "-m", "direct requests call")

    candidate = Finding(
        decision_id="0007",
        severity="high",
        file="src/service.py",
        lines="1-2",
        claim="calls requests.get directly instead of the shared client",
        decision_quote="All outbound HTTP MUST go through `src/http/client.py`.",
        suggested_resolution="use src/http/client.py",
    )
    verified = VerifiedFinding(**candidate.model_dump(), upheld=True, verification_note="confirmed")

    engine = FakeEngine(
        review_result=ReviewResult(findings=[candidate]),
        verify_result=VerifyResult(verified=[verified]),
    )
    cfg = Config(repo_root=repo_with_decision, propose_decisions=False)

    result = run_pipeline(cfg, engine, repo_with_decision, base="main", head="feature")

    assert len(result.selected_decisions) == 1
    assert len(engine.review_calls) == 1
    assert len(engine.verify_calls) == 1
    assert result.findings == [verified]


def test_pipeline_runs_propose_stage_when_enabled(repo_with_decision: Path) -> None:
    _git(repo_with_decision, "checkout", "-q", "-b", "feature")
    (repo_with_decision / "README.md").write_text("updated\n")
    _git(repo_with_decision, "add", "-A")
    _git(repo_with_decision, "commit", "-q", "-m", "touch readme")

    proposal = ProposedDecision(
        title="New pattern",
        rationale="looks significant",
        suggested_scope=[],
        draft_body="## Decision\n...",
    )
    engine = FakeEngine(propose_result=ProposeResult(proposals=[proposal]))
    cfg = Config(repo_root=repo_with_decision, propose_decisions=True)

    result = run_pipeline(cfg, engine, repo_with_decision, base="main", head="feature")

    assert result.proposals == [proposal]
    assert len(engine.propose_calls) == 1
