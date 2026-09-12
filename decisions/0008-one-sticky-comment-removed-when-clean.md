---
id: "0008"
title: One sticky comment per PR, removed entirely on a clean run
status: accepted
date: 2026-09-12
scope:
  - "src/decision_agent/render.py"
  - "src/decision_agent/github.py"
  - "src/decision_agent/cli.py"
tags: [github, comments]
---

## Context

The workflow runs on every `opened`/`synchronize`/`reopened` event, so a
PR pushed five times will trigger this tool five times. The alternative
actually considered was posting a fresh comment on every run — simpler to
implement, and it would also mean posting an explicit "No issues found"
comment on a clean run for visibility/reassurance that the bot actually
ran. Both were rejected: a growing thread of bot comments trains
reviewers to tune the bot out (the opposite of the tool's entire purpose),
and a persistent "all good" comment becomes actively misleading the
moment someone pushes a new, unreviewed commit without the bot having
re-run yet.

## Decision

`cli.py`'s `review` command MUST upsert a single comment per PR keyed by
the `MARKER` constant (`<!-- decision-agent:v1 -->`): `github.py`'s
`upsert_comment` MUST `PATCH` the comment `find_existing_comment` locates
when one exists, and only `POST` a new comment when none does. When
`render_comment` returns `None` — no `VerifiedFinding` both upheld and at
or above `severity_threshold`, and no proposals — the CLI MUST call
`remove_comment` and `remove_label` rather than leaving a stale
comment/label in place or posting an explicit "clean" comment in its
stead.

## Consequences

A PR that goes from flagged to clean loses that history entirely — the
comment is deleted, not archived or collapsed — so there is no record in
the PR thread of what used to be flagged and got fixed. Every run also
pays the cost of listing and paging through a PR's existing comments
just to find (or fail to find) the one with the marker before deciding
whether to PATCH, POST, or DELETE anything.
