"""Frontmatter parsing, scope-glob matching, superseded-status exclusion."""

from __future__ import annotations

from pathlib import Path

import pytest

from decision_agent.decisions import (
    DecisionParseError,
    load_decisions,
    parse_decision_file,
    select_decisions,
)

HTTP_DECISION = """\
---
id: "0007"
title: All outbound HTTP goes through the shared client
status: accepted
date: 2026-03-14
scope:
  - "src/**/*.py"
tags: [http, networking]
---

## Decision
All outbound HTTP MUST go through `src/http/client.py`.
"""

REPO_WIDE_DECISION = """\
---
id: "0002"
title: No secrets in source control
status: accepted
date: 2026-01-01
---

## Decision
Secrets MUST NOT be committed.
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


def write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def test_parse_decision_file_basic_fields(tmp_path: Path) -> None:
    path = write(tmp_path, "0007-http.md", HTTP_DECISION)
    decision = parse_decision_file(path)

    assert decision.id == "0007"
    assert decision.title == "All outbound HTTP goes through the shared client"
    assert decision.status == "accepted"
    assert decision.scope == ["src/**/*.py"]
    assert decision.tags == ["http", "networking"]
    assert "MUST go through" in decision.body


def test_parse_decision_file_missing_frontmatter(tmp_path: Path) -> None:
    path = write(tmp_path, "bad.md", "# just a heading, no frontmatter\n")
    with pytest.raises(DecisionParseError):
        parse_decision_file(path)


def test_parse_decision_file_missing_required_field(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "bad.md",
        "---\ntitle: no id or status\ndate: 2026-01-01\n---\nbody\n",
    )
    with pytest.raises(DecisionParseError):
        parse_decision_file(path)


def test_scope_glob_matching(tmp_path: Path) -> None:
    path = write(tmp_path, "0007-http.md", HTTP_DECISION)
    decision = parse_decision_file(path)

    assert decision.applies_to(["src/service/handler.py"]) is True
    assert decision.applies_to(["README.md"]) is False
    assert decision.applies_to(["README.md", "src/a.py"]) is True


def test_empty_scope_applies_repo_wide(tmp_path: Path) -> None:
    path = write(tmp_path, "0002-secrets.md", REPO_WIDE_DECISION)
    decision = parse_decision_file(path)

    assert decision.applies_to(["anything/at/all.txt"]) is True
    assert decision.applies_to([]) is True


def test_load_decisions_missing_directory_returns_empty(tmp_path: Path) -> None:
    assert load_decisions(tmp_path / "nonexistent") == []


def test_select_decisions_excludes_superseded_and_out_of_scope(tmp_path: Path) -> None:
    write(tmp_path, "0001-old.md", SUPERSEDED_DECISION)
    write(tmp_path, "0002-secrets.md", REPO_WIDE_DECISION)
    write(tmp_path, "0007-http.md", HTTP_DECISION)

    decisions = load_decisions(tmp_path)
    assert len(decisions) == 3

    selected = select_decisions(decisions, ["src/service/handler.py"])
    selected_ids = {d.id for d in selected}

    assert selected_ids == {"0002", "0007"}  # superseded 0001 dropped


def test_select_decisions_drops_decisions_out_of_scope(tmp_path: Path) -> None:
    write(tmp_path, "0007-http.md", HTTP_DECISION)
    decisions = load_decisions(tmp_path)

    assert select_decisions(decisions, ["docs/readme.md"]) == []
