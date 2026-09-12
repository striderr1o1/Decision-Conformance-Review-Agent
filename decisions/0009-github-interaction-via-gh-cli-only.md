---
id: "0009"
title: All GitHub interaction goes through the gh CLI, never an API client library
status: accepted
date: 2026-09-12
scope:
  - "src/decision_agent/github.py"
  - "pyproject.toml"
tags: [github, cli]
---

## Context

`github.py` needs to list and post PR comments and manage a label from
inside a GitHub Actions job. The alternative actually considered was
adding a typed GitHub API client dependency — `PyGithub`, or raw
`requests` against the REST API — which would give typed responses
instead of hand-parsed CLI JSON output and avoid shelling out entirely.
That was rejected because `gh` ships preinstalled on GitHub-hosted
runners and auto-discovers `GITHUB_TOKEN` from the Actions environment,
so using it costs zero auth code and zero new entries in `pyproject.toml`'s
`dependencies` — exactly the same "engine kept portable, minimal
dependencies" preference that shaped the rest of this project.

## Decision

All GitHub interaction MUST go through the `gh` CLI via `subprocess` (see
`_run_gh` in `github.py`). `github.py` MUST NOT import `requests`,
`PyGithub`, `httpx`, or any other HTTP or GitHub API client library, and
`pyproject.toml`'s `dependencies` MUST NOT gain one of those for this
purpose.

## Consequences

Every GitHub response has to be hand-parsed from `gh api`'s JSON text
output rather than deserialized into a typed object — already the cause
of one real bug (`find_existing_comment` originally used `--paginate`
alone, which concatenates pages into invalid JSON past the first page;
fixed by switching to `--slurp`). The tool's correctness is tied to
`gh`'s exact CLI flags and output shape staying stable across whatever
runner image GitHub ships, and to `gh` actually being on `PATH` wherever
this runs — true on GitHub-hosted runners today, not guaranteed anywhere
else.
