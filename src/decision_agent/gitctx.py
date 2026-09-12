"""Resolve the base ref, compute the merge-base diff, and list changed paths.

Uses the three-dot diff form (`base...head`), which is "what head added
since it diverged from base" — as opposed to two-dot (`base..head`), which
also includes unrelated commits that landed on base after the branches
split. Three-dot is what we want: reviewing a PR should only ever look at
what the PR actually changed.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


@dataclass
class DiffContext:
    base: str
    head: str
    merge_base: str
    diff: str
    changed_files: list[str]

    @property
    def diff_bytes(self) -> int:
        return len(self.diff.encode("utf-8"))


def _run_git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def resolve_base_ref(explicit_base: str | None = None) -> str:
    """Resolve the base ref to diff against.

    Priority: explicit CLI arg > GITHUB_BASE_REF (Actions PR event) > "main".
    """
    if explicit_base:
        return explicit_base
    if base := os.environ.get("GITHUB_BASE_REF"):
        return base
    return "main"


def resolve_head_ref(explicit_head: str | None = None) -> str:
    """Resolve the head ref to diff. Priority: explicit CLI arg >
    GITHUB_HEAD_REF (Actions PR event) > "HEAD"."""
    if explicit_head:
        return explicit_head
    if head := os.environ.get("GITHUB_HEAD_REF"):
        return head
    return "HEAD"


def _ref_exists(repo_root: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _resolvable_base(repo_root: Path, base: str) -> str:
    """`base` might be a bare branch name that only exists as `origin/<base>`
    in CI (no local tracking branch). Prefer whichever actually resolves."""
    for candidate in (base, f"origin/{base}"):
        if _ref_exists(repo_root, candidate):
            return candidate
    raise GitError(
        f"base ref {base!r} does not resolve locally or as origin/{base}; "
        "did you check out with fetch-depth: 0?"
    )


def get_diff_context(
    repo_root: Path,
    base: str | None = None,
    head: str | None = None,
) -> DiffContext:
    resolved_base = _resolvable_base(repo_root, resolve_base_ref(base))
    resolved_head = resolve_head_ref(head)

    merge_base = _run_git(repo_root, ["merge-base", resolved_base, resolved_head]).strip()

    diff = _run_git(
        repo_root,
        ["diff", f"{resolved_base}...{resolved_head}", "--"],
    )

    changed_output = _run_git(
        repo_root,
        ["diff", "--name-only", f"{resolved_base}...{resolved_head}", "--"],
    )
    changed_files = [line for line in changed_output.splitlines() if line.strip()]

    return DiffContext(
        base=resolved_base,
        head=resolved_head,
        merge_base=merge_base,
        diff=diff,
        changed_files=changed_files,
    )
