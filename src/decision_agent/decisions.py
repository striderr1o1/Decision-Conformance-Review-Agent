"""Load decision records: frontmatter parsing, scope-glob matching, status filter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from decision_agent.globmatch import match_any

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n?(.*)$", re.DOTALL)

ACTIVE_STATUSES = {"accepted", "proposed"}


class DecisionParseError(ValueError):
    pass


@dataclass
class Decision:
    id: str
    title: str
    status: str
    date: str
    scope: list[str]
    tags: list[str]
    body: str
    path: Path

    def applies_to(self, changed_files: list[str]) -> bool:
        """A decision with no scope applies repo-wide. Otherwise it applies
        if any changed file matches any scope glob."""
        if not self.scope:
            return True
        return any(match_any(self.scope, f) for f in changed_files)

    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


def parse_decision_file(path: Path) -> Decision:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise DecisionParseError(f"{path}: missing YAML frontmatter (expected leading '---')")

    frontmatter_raw, body = match.groups()
    try:
        meta = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError as exc:
        raise DecisionParseError(f"{path}: invalid YAML frontmatter: {exc}") from exc

    if not isinstance(meta, dict):
        raise DecisionParseError(f"{path}: frontmatter must be a mapping")

    missing = [key for key in ("id", "title", "status", "date") if key not in meta]
    if missing:
        raise DecisionParseError(f"{path}: frontmatter missing required field(s): {missing}")

    scope = meta.get("scope") or []
    if isinstance(scope, str):
        scope = [scope]
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]

    return Decision(
        id=str(meta["id"]),
        title=str(meta["title"]),
        status=str(meta["status"]),
        date=str(meta["date"]),
        scope=list(scope),
        tags=list(tags),
        body=body.strip(),
        path=path,
    )


def load_decisions(decisions_dir: Path) -> list[Decision]:
    """Parse every `*.md` file in `decisions_dir`, sorted by filename.
    Returns an empty list if the directory doesn't exist."""
    if not decisions_dir.is_dir():
        return []

    decisions = []
    for path in sorted(decisions_dir.glob("*.md")):
        decisions.append(parse_decision_file(path))
    return decisions


def select_decisions(decisions: list[Decision], changed_files: list[str]) -> list[Decision]:
    """Deterministic prefilter: drop superseded decisions and decisions
    whose scope doesn't touch any changed file. This runs before any LLM
    call is made and is the first line of defense against noise."""
    return [d for d in decisions if d.is_active() and d.applies_to(changed_files)]


def filter_ignored_paths(paths: list[str], ignore_patterns: list[str]) -> list[str]:
    """Drop changed paths matching a configured ignore glob (lockfiles,
    minified bundles, ...) before they're considered for scope matching or
    sent anywhere near the review stage."""
    if not ignore_patterns:
        return paths
    return [p for p in paths if not match_any(ignore_patterns, p)]
