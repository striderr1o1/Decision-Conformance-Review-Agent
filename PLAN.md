# Decision-Conformance Review Agent

## Context

Teams record architectural decisions and then quietly violate them. The decision doc says "all HTTP goes through the shared client"; six months later there are four `requests.get` calls in three services, and nobody noticed because code review looks at the diff, not at what the team already agreed to.

This project builds an agent that closes that gap. Decisions live as files in the repo. When a teammate opens or pushes to a pull request, a GitHub Action runs the agent, which reads the recorded decisions, reads the change, and flags where the change contradicts something already decided — citing the specific decision and offering a resolution. It also notices when a PR makes a significant architectural choice that *isn't* written down anywhere, and drafts a decision record for it, so the corpus stays alive instead of rotting.

Intended outcome: a non-blocking reviewer the team trusts. Success is measured in precision, not recall — if three of the first five flags are wrong, people mute it and the project is dead.

**Starting point:** greenfield. The repo contains only `rough.md` and this plan.

### Decisions already made

| Question | Choice |
|---|---|
| Decision source | Markdown files in `decisions/` in the target repo |
| Deployment | GitHub Actions; engine kept portable behind a CLI |
| Engine | Claude Code headless (`claude -p`), not the Python SDK |
| Credential | `claude setup-token` now; swap to `ANTHROPIC_API_KEY` later |
| Output | Sticky PR comment + label, non-blocking |
| Scope | Flag violations **and** propose new decision records |
| Language | Python |

**Why headless over the `anthropic` SDK:** the Python SDK requires a console API key, which we don't have — we have a Claude subscription. Claude Code accepts *either* a subscription token or an API key, so the credential becomes a swappable env var rather than a rewrite. It is also a tool-using agent, which matters here: "does this violate our HTTP client decision" often can't be answered from the diff hunks alone, and the agent can open the surrounding files.

---

## Architecture

Six stages, each independently testable, orchestrated by a plain Python function. No agent framework.

```
collect  →  select  →  review  →  verify  →  propose  →  report
```

1. **collect** — resolve the base ref, compute `git diff <merge-base>...HEAD`, list changed paths.
2. **select** — deterministic prefilter: match each decision's `scope` globs against the changed paths. Cheap, no tokens, and it is the first line of defense against noise.
3. **review** — one headless Claude call with the selected decisions + diff, returning typed findings via `--json-schema`.
4. **verify** — second call that tries to *falsify* each candidate finding. Drop anything unsupported. **This is the main precision lever.**
5. **propose** — detect significant undocumented decisions in the diff, draft decision records.
6. **report** — render markdown, upsert the sticky comment, set the label.

### Two deliberate constraints

**The engine never executes PR code.** The reviewer reads code written by whoever opened the PR. It runs with `--restricted --tools Read,Grep,Glob`, which removes Bash and every other code-running tool and confines file access to the working directory. This is not optional hardening; it is the difference between a review bot and a remote code execution hole.

**One comment per PR, edited in place.** Push five times, get one comment — always current. A marker (`<!-- decision-agent:v1 -->`) in the comment body is grepped on each run and the existing comment is edited rather than a new one posted. This single detail decides whether the bot is tolerable.

---

## Decision file format

`decisions/0007-shared-http-client.md`:

```markdown
---
id: "0007"
title: All outbound HTTP goes through the shared client
status: accepted          # accepted | superseded | proposed
date: 2026-03-14
scope:                    # globs; omit to apply repo-wide
  - "src/**/*.py"
tags: [http, networking]
---

## Context
...why this came up...

## Decision
All outbound HTTP MUST go through `src/http/client.py`. Direct use of
`requests`, `httpx`, or `urllib` outside that module is prohibited.

## Consequences
...what this costs us...
```

`scope` is the load-bearing field: it lets stage 2 discard irrelevant decisions before any tokens are spent. `status: superseded` decisions are excluded from review but retained for history.

---

## Files to create

```
pyproject.toml
.decision-agent.toml                    # config, checked into target repo
decisions/
  0001-decisions-live-in-this-directory.md   # seed record; dogfoods the format
src/decision_agent/
  cli.py              # entry point: `decision-agent review`
  config.py           # load .decision-agent.toml, env overrides
  gitctx.py           # base-ref resolution, merge-base diff, changed paths
  decisions.py        # frontmatter parsing, scope matching, status filter
  schema.py           # pydantic models + JSON Schema export for --json-schema
  engine.py           # subprocess wrapper around `claude -p`
  review.py           # stage orchestration
  render.py           # findings -> markdown comment body
  github.py           # sticky-comment upsert + label, via `gh api`
  prompts/
    review.md
    verify.md
    propose.md
tests/
  fixtures/           # recorded diffs, decision sets, canned engine JSON
  test_gitctx.py  test_decisions.py  test_render.py  test_review.py
.github/workflows/decision-review.yml
```

### Key implementation notes

**`gitctx.py`** — use the three-dot form, `git diff origin/$BASE...HEAD`. Three dots means "what this branch added since it diverged"; two dots drags in unrelated changes from main and will generate phantom findings. In Actions, base and head come from `GITHUB_BASE_REF` / `GITHUB_HEAD_REF`; locally they're CLI args. Requires `fetch-depth: 0` in checkout.

**`engine.py`** — shells out to:

```bash
claude -p \
  --output-format json \
  --json-schema "$SCHEMA" \
  --model sonnet \
  --fallback-model opus \
  --restricted --tools Read,Grep,Glob \
  --permission-mode dontAsk \
  --permission-prompts none \
  --system-prompt-file prompts/review.md
```

`--json-schema` gives validated structured output directly — no regex-scraping model prose. `--permission-prompts none` guarantees the run can never hang waiting for input in CI. Keep the credential read from env only (`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`), never a flag, so the swap later is a secret change.

**`schema.py`** — findings shape:

```python
class Finding(BaseModel):
    decision_id: str
    severity: Literal["high", "medium", "low"]
    file: str
    lines: str                  # "42-58"
    claim: str                  # what this diff does that conflicts
    decision_quote: str         # verbatim line from the decision file
    suggested_resolution: str
```

`decision_quote` must be verbatim — it makes stage 4 able to check that the cited decision actually says what the model claims, and makes a wrong flag obvious to a human at a glance.

**`github.py`** — use `gh api`, which is preinstalled on runners and picks up `GITHUB_TOKEN` automatically; no auth code and no extra dependency. Upsert = list comments, find the marker, `PATCH` if present else `POST`.

**`render.py`** — group findings by severity, high first. Below the configured `severity_threshold`, post nothing at all and remove any stale comment — silence on a clean PR is a feature.

### Config (`.decision-agent.toml`)

```toml
decisions_dir       = "decisions"
model               = "sonnet"
severity_threshold  = "medium"
max_diff_bytes      = 400_000     # above this, review changed files individually
propose_decisions   = true
label               = "decision-conflict"
ignore_paths        = ["**/*.lock", "dist/**", "**/*.min.js"]
```

### Workflow

```yaml
name: Decision Review
on:
  pull_request:
    types: [opened, synchronize, reopened]
permissions:
  contents: read
  pull-requests: write
concurrency:
  group: decision-review-${{ github.event.pull_request.number }}
  cancel-in-progress: true
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -e .
      - run: npm install -g @anthropic-ai/claude-code
      - run: decision-agent review
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

**Fork PRs won't work** and that's expected: GitHub withholds secrets and downgrades the token on PRs from forks. Irrelevant for a team pushing branches directly. If it ever matters, the fix is a second `workflow_run` workflow — defer it.

---

## Build order

Each milestone ends somewhere useful.

**M1 — Skeleton, no LLM.** `pyproject.toml`, `cli.py`, `gitctx.py`, `decisions.py`, `render.py`. `decision-agent review --base main --dry-run` with a stubbed engine prints a rendered comment to stdout. Proves diff and decision plumbing without spending a token.

**M2 — Real review pass.** `schema.py`, `engine.py`, `prompts/review.md`. Same command, real findings printed locally. Run it against hand-built diffs that *should* and *should not* trip a decision.

**M3 — Precision.** Add the verify stage, scope prefiltering, and `severity_threshold`. Tune against real branches until clean PRs are reliably silent. **Do not skip ahead of this milestone** — everything downstream is worthless if the flags are wrong.

**M4 — Ship to CI.** `github.py`, workflow YAML, `claude setup-token` → repo secret. Watch it on real PRs.

**M5 — Proposed decisions.** `prompts/propose.md`; drafts rendered in a collapsed `<details>` block in the same sticky comment, never as separate noise.

**M6 — Tune.** Track dismissed flags, feed the patterns back into the prompts.

---

## Verification

**Unit tests** (`pytest`, no network): fixtures of recorded diffs + decision sets + canned engine JSON. Cover merge-base diff parsing, frontmatter and scope-glob matching, superseded-status exclusion, severity thresholding, and comment rendering. The engine subprocess is mocked — these must run offline and fast.

**Engine contract test:** one test that actually invokes `claude -p` with a trivial schema, marked `@pytest.mark.live` and excluded from the default run, to catch CLI flag drift.

**Local end-to-end:** run `decision-agent review --base main --dry-run` against a real branch in a real repo. This is the primary tuning loop — no pushing, no CI wait.

**Deliberate positive/negative pair:** craft one branch that plainly violates a seeded decision (a direct `requests.get` against the shared-HTTP-client decision) and one that touches the same files without violating anything. The first must flag; the second must produce **no comment at all**. This pair is the regression test for the whole product.

**CI check:** open a scratch PR, confirm the comment appears, push again, confirm it's *edited* rather than duplicated, then push a fix and confirm the comment is removed.
