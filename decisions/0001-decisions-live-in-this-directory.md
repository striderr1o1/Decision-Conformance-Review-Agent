---
id: "0001"
title: Architectural decisions live as markdown files in decisions/
status: accepted
date: 2026-09-12
scope: []
tags: [process, decision-agent]
---

## Context

Teams record architectural decisions in various places — wiki pages, Slack
threads, tribal knowledge — and those records drift out of sync with the
code, or nobody consults them at review time. We want a single, versioned
source of truth that a human can read in a PR diff and that a tool can
parse deterministically.

## Decision

Every architectural decision that constrains future code MUST be recorded
as a markdown file in `decisions/`, named `NNNN-short-slug.md`, with YAML
frontmatter containing at minimum `id`, `title`, `status`, and `date`. A
decision's `scope` field (a list of glob patterns) determines which changed
files cause it to be considered during review; an empty or omitted `scope`
means the decision applies repo-wide. A decision that has been replaced
MUST be marked `status: superseded` rather than deleted, so history is
preserved.

## Consequences

Every new architectural constraint costs a small amount of upfront
authoring effort. In exchange, the constraint is machine-readable, has a
canonical location, and can be checked automatically against future pull
requests instead of relying on reviewers to remember it.
