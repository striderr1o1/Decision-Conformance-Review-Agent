---
id: "0006"
title: Candidate findings must survive adversarial verification and carry a verbatim quote
status: accepted
date: 2026-09-12
scope:
  - "src/decision_agent/review.py"
  - "src/decision_agent/render.py"
  - "src/decision_agent/schema.py"
tags: [precision, review]
---

## Context

This tool's stated success metric is precision, not recall: a team mutes
a reviewer the first time it's confidently wrong a couple of times in a
row. The alternative actually considered was rendering the first-pass
review call's findings directly — one LLM call instead of two, half the
latency and spend, and the review prompt already instructs the model to
be conservative and "when in doubt, leave it out." That was rejected
because a single pass, however conservatively prompted, has nothing
structurally stopping a confident-but-wrong citation from reaching a
human; the whole point of a second, differently-instructed pass is to
try to falsify each candidate rather than trust the same reasoning twice.

## Decision

`run_pipeline` MUST route every candidate `Finding` produced by the
review stage through `run_verify_stage` before it is eligible to appear
anywhere; `render_comment` MUST only include a `VerifiedFinding` whose
`upheld` is `true` (and whose severity meets `severity_threshold`) —
it MUST NOT render a raw candidate `Finding` that hasn't been through
verification. Separately, a finding's `decision_quote` MUST be a verbatim
substring of the cited decision's body, and the verify stage MUST set
`upheld=false` when that quote does not literally appear in the decision
file it cites (a paraphrase does not count, per the quote-fidelity check
in `prompts/verify.md`).

## Consequences

Every finding that reaches review costs two LLM calls instead of one, so
latency and spend roughly double for any diff that trips a decision at
all. It also means a finding that is substantively correct can still be
dropped if the first-pass model paraphrases the decision instead of
copying it verbatim — the design deliberately trades away some recall
(a few true positives lost to quote mismatch) to keep what does surface
trustworthy.
