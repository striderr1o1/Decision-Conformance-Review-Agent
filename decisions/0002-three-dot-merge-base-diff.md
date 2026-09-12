---
id: "0002"
title: Diff the merge-base range with three dots, not two
status: accepted
date: 2026-09-12
scope:
  - "src/decision_agent/gitctx.py"
tags: [git, diff]
---

## Context

Reviewing a PR must only look at what that PR actually changed. Given
`git diff <base>..<head>` (two dots), the comparison is literally "head vs.
base right now" — if unrelated commits have landed on `base` since the
branch diverged, those commits' content shows up as part of the "diff"
too, even though the PR author never touched them. That produces phantom
findings about code the PR didn't introduce, which is exactly the kind of
noise this tool exists to avoid. The alternative actually considered was
using the simpler two-dot form directly — it's one character shorter,
more commonly reached for, and doesn't require computing a merge-base at
all.

## Decision

`gitctx.get_diff_context` MUST compute both the diff and the changed-file
list using the three-dot form — `git diff <base>...<head> --` and
`git diff --name-only <base>...<head> --` — which git resolves against the
merge-base of the two refs. Code in this module MUST NOT switch to the
two-dot form (`<base>..<head>`) for either call, even as a simplification,
since two-dot silently reintroduces any commits that landed on `base`
after the branches diverged into both the diff and the changed-files list
that drives decision selection.

## Consequences

Every caller has to reason in terms of "what head added since it
diverged from base," not "head vs. base as they stand right now" — a
long-lived PR whose base has since moved far ahead will not be diffed
against base's current tip, which can surprise someone comparing the
tool's output to a naive `git diff main..feature` run locally. It also
means the tool will never flag something that was already wrong on
`base` before the PR branched — only what head's own commits introduced.
