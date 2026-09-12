"""Merge-base diff and changed-path resolution, offline (real git, temp repo)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from decision_agent.gitctx import GitError, get_diff_context, resolve_base_ref, resolve_head_ref


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("line1\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    return tmp_path


def test_changed_files_and_diff_on_simple_branch(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.txt").write_text("new file\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "add b")

    ctx = get_diff_context(repo, base="main", head="feature")

    assert ctx.changed_files == ["b.txt"]
    assert "new file" in ctx.diff
    assert ctx.base == "main"
    assert ctx.head == "feature"


def test_three_dot_excludes_unrelated_base_commits(repo: Path) -> None:
    """The whole point of `base...head`: commits that land on base *after*
    the branches diverged must not show up as part of the PR's diff."""
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "feature.txt").write_text("feature work\n")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-q", "-m", "feature work")

    _git(repo, "checkout", "-q", "main")
    (repo / "unrelated.txt").write_text("unrelated main work\n")
    _git(repo, "add", "unrelated.txt")
    _git(repo, "commit", "-q", "-m", "unrelated work landed on main")

    ctx = get_diff_context(repo, base="main", head="feature")

    assert ctx.changed_files == ["feature.txt"]
    assert "unrelated.txt" not in ctx.diff


def test_missing_base_raises_git_error(repo: Path) -> None:
    with pytest.raises(GitError):
        get_diff_context(repo, base="does-not-exist", head="HEAD")


def test_resolve_base_ref_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    assert resolve_base_ref() == "main"
    assert resolve_base_ref("explicit") == "explicit"

    monkeypatch.setenv("GITHUB_BASE_REF", "from-env")
    assert resolve_base_ref() == "from-env"
    assert resolve_base_ref("explicit") == "explicit"


def test_resolve_head_ref_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_HEAD_REF", raising=False)
    assert resolve_head_ref() == "HEAD"

    monkeypatch.setenv("GITHUB_HEAD_REF", "pr-branch")
    assert resolve_head_ref() == "pr-branch"
    assert resolve_head_ref("explicit") == "explicit"
