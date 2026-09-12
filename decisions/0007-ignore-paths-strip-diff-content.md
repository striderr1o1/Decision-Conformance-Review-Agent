---
id: "0007"
title: Ignored paths are stripped from the diff content, not just the file list
status: accepted
date: 2026-09-12
scope:
  - "src/decision_agent/review.py"
  - "src/decision_agent/decisions.py"
tags: [privacy, diff]
---

## Context

`ignore_paths` (lockfiles, `dist/**`, `**/*.min.js` by default) exists so
generated or irrelevant files don't burn review tokens or produce
findings on code nobody wants reviewed. The alternative actually
available, and the smaller change, was to drop ignored paths only from
the changed-files list that `select_decisions` uses for scope matching —
that's all the *select* stage strictly needs to behave correctly, since a
decision only gets selected by a changed path appearing in that list.
That was rejected because the full diff text — ignored file's contents
included — would still ride along into every review/verify/propose
prompt: burning context budget on, say, a multi-thousand-line lockfile,
and risking a finding (or incidental exposure of whatever that file
happens to contain) on a path the user explicitly declared out of scope.

## Decision

`run_pipeline` MUST call `strip_ignored_files` on the diff text itself
(removing whole `diff --git ...` sections whose path matches
`ignore_paths`), in addition to calling `filter_ignored_paths` on the
changed-file list, before either is used to build any prompt. The two
MUST be kept in lockstep — `run_pipeline` MUST NOT reach a state where a
path has been removed from `changed_files` but that path's content is
still present in `diff_ctx.diff` sent downstream, or vice versa.

## Consequences

Stripping by path depends on `_diff_file_path` correctly recovering a
path from each diff section's `+++`/`---` lines (falling back to the
ambiguous `diff --git a/<path> b/<path>` header only when neither is
present); a diff shape that heuristic can't handle would let an ignored
file's content through unstripped. The ignore filter's correctness is
therefore tied to that header-parsing heuristic continuing to cover every
diff shape `git diff` can produce, not just the common ones exercised in
tests.
