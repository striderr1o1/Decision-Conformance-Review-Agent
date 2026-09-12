"""Stage orchestration: chunking, prompt building, and the full pipeline
against a fake engine (no subprocess, no network, no tokens)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from decision_agent.config import Config
from decision_agent.review import (
    _diff_file_path,
    build_review_prompt,
    chunk_diff,
    run_pipeline,
    strip_ignored_files,
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


def test_strip_ignored_files_drops_whole_matching_section() -> None:
    kept = "diff --git a/src/app.py b/src/app.py\n+x = 1\n"
    ignored = "diff --git a/poetry.lock b/poetry.lock\n+aaaaaaaaaaaaaaaaaaaaaaaa\n"
    diff = kept + ignored

    result = strip_ignored_files(diff, ["**/*.lock"])

    assert "poetry.lock" not in result
    assert "aaaaaaaaaaaaaaaaaaaaaaaa" not in result
    assert "src/app.py" in result
    assert "x = 1" in result


def test_strip_ignored_files_noop_without_patterns() -> None:
    diff = "diff --git a/poetry.lock b/poetry.lock\n+x\n"
    assert strip_ignored_files(diff, []) == diff


def test_strip_ignored_files_uses_plus_plus_plus_line_not_ambiguous_header() -> None:
    """A path with a space makes the `diff --git a/<p> b/<p>` header line
    ambiguous to split; the `+++ b/<path>` line is unambiguous and must be
    preferred."""
    diff = (
        "diff --git a/dist/my app.min.js b/dist/my app.min.js\n"
        "--- a/dist/my app.min.js\n"
        "+++ b/dist/my app.min.js\n"
        "+minified\n"
    )
    result = strip_ignored_files(diff, ["dist/**", "**/*.min.js"])
    assert "minified" not in result
    assert result == ""


def test_diff_file_path_ignores_added_line_that_looks_like_a_plus_plus_plus_header() -> None:
    """A hunk can add a line whose own content happens to start with `++`,
    which renders as `+++ ...` — indistinguishable by prefix alone from the
    real `+++ b/<path>` header. Only the lines before the first `@@` hunk
    marker are metadata; anything after is hunk content and must be
    ignored when hunting for the path."""
    diff = (
        "diff --git a/poetry.lock b/poetry.lock\n"
        "index 111..222 100644\n"
        "--- a/poetry.lock\n"
        "+++ b/poetry.lock\n"
        "@@ -1,1 +1,2 @@\n"
        " existing\n"
        "+++ b/src/app.py\n"  # content line, not a header
        "+SECRET_LOCKFILE_CONTENT\n"
    )
    assert _diff_file_path(diff) == "poetry.lock"


def test_diff_file_path_ignores_removed_line_that_looks_like_a_dash_dash_dash_header() -> None:
    """Mirror case: a removed line whose own content starts with `--`
    renders as `--- ...` and must likewise be ignored once past the first
    hunk marker.

    This only exercises `minus_path` if the candidate loop would otherwise
    reach it — which requires `plus_path` to be absent or `/dev/null`, i.e.
    a deleted file. A diff with a normal `+++ b/<path>` header would return
    from `plus_path` before `minus_path` is ever consulted, making the
    planted content line inert either way."""
    diff = (
        "diff --git a/poetry.lock b/poetry.lock\n"
        "deleted file mode 100644\n"
        "index 111..000\n"
        "--- a/poetry.lock\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-keep\n"
        "--- b/src/app.py\n"  # content line, not a header
        "-SECRET_LOCK\n"
    )
    assert _diff_file_path(diff) == "poetry.lock"

    result = strip_ignored_files(diff, ["**/*.lock"])
    assert "SECRET_LOCK" not in result


def test_strip_ignored_files_not_fooled_by_content_line_matching_header_shape() -> None:
    """End-to-end version of the two tests above: the ignored lockfile
    section must actually be removed, not misattributed to src/app.py and
    kept."""
    diff = (
        "diff --git a/poetry.lock b/poetry.lock\n"
        "index 111..222 100644\n"
        "--- a/poetry.lock\n"
        "+++ b/poetry.lock\n"
        "@@ -1,1 +1,2 @@\n"
        " existing\n"
        "+++ b/src/app.py\n"
        "+SECRET_LOCKFILE_CONTENT\n"
    )
    result = strip_ignored_files(diff, ["**/*.lock"])
    assert "SECRET_LOCKFILE_CONTENT" not in result
    assert result == ""


def test_pipeline_strips_ignored_files_from_diff_sent_to_engine(
    repo_with_decision: Path,
) -> None:
    """`ignore_paths` must strip the lockfile's contents out of the diff
    handed to the engine, not just drop it from the changed-files list used
    for scope selection."""
    _git(repo_with_decision, "checkout", "-q", "-b", "feature")
    src_dir = repo_with_decision / "src"
    src_dir.mkdir()
    (src_dir / "service.py").write_text("import requests\nrequests.get('http://x')\n")
    (repo_with_decision / "poetry.lock").write_text("lockfile-secret-marker\n" * 5)
    _git(repo_with_decision, "add", "-A")
    _git(repo_with_decision, "commit", "-q", "-m", "change plus lockfile")

    engine = FakeEngine()
    cfg = Config(repo_root=repo_with_decision, propose_decisions=False)

    result = run_pipeline(cfg, engine, repo_with_decision, base="main", head="feature")

    assert "poetry.lock" not in result.diff_context.diff
    assert "lockfile-secret-marker" not in result.diff_context.diff
    assert "poetry.lock" not in result.diff_context.changed_files
    assert "src/service.py" in result.diff_context.changed_files
    assert len(engine.review_calls) == 1
    assert "lockfile-secret-marker" not in engine.review_calls[0]


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
