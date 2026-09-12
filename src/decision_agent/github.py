"""Sticky-comment upsert and label management via `gh api`.

`gh` is preinstalled on GitHub-hosted runners and picks up `GITHUB_TOKEN`
automatically, so this module needs no auth code and no extra dependency.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

PR_REF_RE = re.compile(r"refs/pull/(\d+)/")


class GithubError(RuntimeError):
    pass


def _run_gh(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise GithubError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def resolve_pr_number(explicit: int | None = None) -> int:
    """Priority: explicit CLI arg > GITHUB_REF (Actions PR event) > `gh pr view`."""
    if explicit:
        return explicit
    if match := PR_REF_RE.search(os.environ.get("GITHUB_REF", "")):
        return int(match.group(1))
    out = _run_gh(["pr", "view", "--json", "number", "-q", ".number"])
    return int(out.strip())


def _repo_slug() -> str:
    if slug := os.environ.get("GITHUB_REPOSITORY"):
        return slug
    out = _run_gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    return out.strip()


def find_existing_comment(pr_number: int, marker: str) -> int | None:
    slug = _repo_slug()
    out = _run_gh(["api", f"repos/{slug}/issues/{pr_number}/comments", "--paginate"])
    comments = json.loads(out) if out.strip() else []
    for comment in comments:
        if marker in comment.get("body", ""):
            return comment["id"]
    return None


def upsert_comment(pr_number: int, body: str, marker: str) -> None:
    """List comments, find the marker, PATCH if present else POST — so a
    PR gets exactly one comment from this bot, always current."""
    slug = _repo_slug()
    existing_id = find_existing_comment(pr_number, marker)
    if existing_id is not None:
        _run_gh(
            [
                "api",
                "--method",
                "PATCH",
                f"repos/{slug}/issues/comments/{existing_id}",
                "-f",
                f"body={body}",
            ]
        )
    else:
        _run_gh(
            [
                "api",
                "--method",
                "POST",
                f"repos/{slug}/issues/{pr_number}/comments",
                "-f",
                f"body={body}",
            ]
        )


def remove_comment(pr_number: int, marker: str) -> None:
    """Remove any existing sticky comment. Called on a clean PR — silence
    is the point, not a stale "all good" comment sitting around forever."""
    slug = _repo_slug()
    existing_id = find_existing_comment(pr_number, marker)
    if existing_id is not None:
        _run_gh(["api", "--method", "DELETE", f"repos/{slug}/issues/comments/{existing_id}"])


def ensure_label(pr_number: int, label: str) -> None:
    slug = _repo_slug()
    _run_gh(
        [
            "api",
            "--method",
            "POST",
            f"repos/{slug}/issues/{pr_number}/labels",
            "-f",
            f"labels[]={label}",
        ]
    )


def remove_label(pr_number: int, label: str) -> None:
    """Best-effort: a 404 (label wasn't applied) is not an error here."""
    slug = _repo_slug()
    subprocess.run(
        ["gh", "api", "--method", "DELETE", f"repos/{slug}/issues/{pr_number}/labels/{label}"],
        capture_output=True,
        text=True,
    )
