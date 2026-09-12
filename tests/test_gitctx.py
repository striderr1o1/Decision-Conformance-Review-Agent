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


def test_head_ref_resolves_on_detached_head_with_no_local_branch(repo: Path) -> None:
    """Reproduces the real CI shape: `actions/checkout` on a `pull_request`
    event leaves a detached HEAD and never creates a local `feature` branch,
    only `origin/feature`. Calling `resolve_head_ref` and asserting the
    string it returns (as the old test did) proves nothing here — the bug
    was in whether that string *resolves*, so this drives it through
    `get_diff_context` against a real repo."""
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.txt").write_text("new file\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "add b")

    # Simulate what `actions/checkout` leaves behind: a remote-tracking ref
    # for the head branch, a detached HEAD at that commit, and no local
    # branch of that name at all.
    _git(repo, "update-ref", "refs/remotes/origin/feature", "refs/heads/feature")
    _git(repo, "checkout", "-q", "--detach", "HEAD")
    _git(repo, "branch", "-D", "feature")

    ctx = get_diff_context(repo, base="main", head="feature")

    assert ctx.head == "origin/feature"
    assert ctx.changed_files == ["b.txt"]
    assert "new file" in ctx.diff


def test_head_ref_falls_back_to_HEAD_when_neither_local_nor_origin_resolves(
    repo: Path,
) -> None:
    """Fork-PR shape: GitHub withholds the fork's ref from `origin` too, so
    neither `feature` nor `origin/feature` exists anywhere. Raising here
    would fail the Action's job on every fork PR; falling back to `HEAD`
    (whatever is actually checked out) reviews the right commits instead."""
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.txt").write_text("new file\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "add b")

    _git(repo, "checkout", "-q", "--detach", "HEAD")
    _git(repo, "branch", "-D", "feature")
    # No origin/feature ref created at all — the fork-PR case.

    ctx = get_diff_context(repo, base="main", head="feature")

    assert ctx.head == "HEAD"
    assert ctx.changed_files == ["b.txt"]
    assert "new file" in ctx.diff


def test_explicit_head_still_resolves_normally(repo: Path) -> None:
    """An explicit --head value pointing at a real local branch must keep
    working exactly as before."""
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.txt").write_text("new file\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "add b")

    ctx = get_diff_context(repo, base="main", head="feature")

    assert ctx.head == "feature"
    assert ctx.changed_files == ["b.txt"]


def test_HEAD_itself_is_never_rewritten_to_origin_form(repo: Path) -> None:
    """The literal ref "HEAD" must be used as-is for local use, never routed
    through the origin/<ref> resolution (there is no `origin/HEAD` concept
    here, and HEAD always resolves on its own)."""
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.txt").write_text("new file\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "add b")

    ctx = get_diff_context(repo, base="main", head="HEAD")

    assert ctx.head == "HEAD"
