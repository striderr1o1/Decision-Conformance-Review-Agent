---
id: "0003"
title: The review engine runs with a read-only tool sandbox, never Bash
status: accepted
date: 2026-09-12
scope:
  - "src/decision_agent/engine.py"
tags: [security, engine]
---

## Context

`ClaudeEngine` shells out to `claude -p` and points it at a checkout of
whatever a PR author pushed — code this project did not write and has no
reason to trust. A tool-using agent is genuinely useful here (the review
prompt explicitly tells it to use Read/Grep/Glob to check whether a
flagged import is actually used the way a diff hunk suggests), but giving
it the same tool access a developer has — in particular Bash, or any tool
that writes or executes — would mean a malicious or merely-buggy PR could
get its own code executed by the reviewer process. The alternative
actually on the table was giving the engine the default Claude Code tool
set (including Bash) so it could, say, run the target's test suite or a
linter to strengthen its findings.

## Decision

Every `claude -p` invocation in `ClaudeEngine._run` MUST include
`--restricted --tools Read,Grep,Glob` (see `ALLOWED_TOOLS` in
`engine.py`) and MUST NOT add Bash, Write, Edit, WebFetch, or any other
execution- or mutation-capable tool to that allowlist, and MUST NOT drop
`--restricted`. This holds for the review, verify, and propose stages
alike — there is no "trusted" stage that gets broader access.

## Consequences

The engine can never run the PR's build, tests, or a linter to
corroborate a finding, and can't fetch external context (no WebFetch) —
it is limited to reading and searching the files already on disk. That
means some findings that a human reviewer could settle by actually running
the code stay speculative, which the review prompt is written to handle
by preferring to drop an unconfirmed finding rather than report it. This
is accepted as the cost of never letting a reviewed PR's own code run
inside the reviewer.
