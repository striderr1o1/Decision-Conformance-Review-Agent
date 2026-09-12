"""Sticky-comment lookup via `gh api --paginate --slurp`, mocked (no real
`gh` invocation, no network)."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from decision_agent.github import find_existing_comment


def _fake_completed(stdout: str, returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


@pytest.fixture(autouse=True)
def _repo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/widgets")


def test_find_existing_comment_parses_realistic_two_page_slurp_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gh api ... --paginate` alone concatenates each page's JSON array
    back to back (`[...][...]`), which isn't valid JSON. `--slurp` wraps
    the pages in one outer array — `[[...], [...]]` — instead. This is that
    realistic shape: two pages of 30 and 5 comments, marker on the second
    page, which is exactly the case that broke before `--slurp` was added
    (anything past the first page)."""
    page1 = [{"id": i, "body": f"noise {i}"} for i in range(30)]
    page2 = [{"id": 1000, "body": "unrelated"}, {"id": 1001, "body": "<!-- decision-agent:v1 -->\nfindings"}]
    payload = json.dumps([page1, page2])

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_completed(payload)

    monkeypatch.setattr(subprocess, "run", fake_run)

    comment_id = find_existing_comment(42, "<!-- decision-agent:v1 -->")

    assert comment_id == 1001
    assert "--paginate" in captured["cmd"]
    assert "--slurp" in captured["cmd"]


def test_find_existing_comment_returns_none_when_marker_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.dumps([[{"id": 1, "body": "no marker here"}]])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_completed(payload))

    assert find_existing_comment(42, "<!-- decision-agent:v1 -->") is None


def test_find_existing_comment_handles_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_completed(""))

    assert find_existing_comment(42, "<!-- decision-agent:v1 -->") is None
