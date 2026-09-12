# decision-agent

Teams write down architectural decisions and then quietly violate them.

The decision doc says *"all HTTP goes through the shared client."* Six months later there are four `requests.get` calls across three services, and nobody noticed — because code review looks at the diff, not at what the team already agreed to. The decision record is still sitting there, still accurate, still ignored.

`decision-agent` closes that gap. Decisions live as markdown files in your repo. When someone opens or pushes to a pull request, a GitHub Action reads those decisions, reads the change, and flags where the change **contradicts something already decided** — citing the specific decision, quoting it verbatim, and suggesting a fix.

It is not a code reviewer. It answers exactly one question: *does this diff contradict a recorded decision?* Code that is ugly, buggy, or untested but breaks no decision produces silence. That narrowness is the point — it is what keeps the tool trustworthy enough to leave switched on.

---

## What it looks like

On a PR that adds `Bash` to a tool allowlist which a decision forbids:

> ## Decision Conformance Review
>
> ### 🔴 High
>
> - **src/decision_agent/engine.py** (lines 30) — ALLOWED_TOOLS is changed from `"Read,Grep,Glob"` to `"Read,Grep,Glob,Bash"`, adding Bash to the allowlist used for every `claude -p` invocation.
>   > Every `claude -p` invocation in `ClaudeEngine._run` MUST include
>   > `--restricted --tools Read,Grep,Glob` … and MUST NOT add Bash, Write,
>   > Edit, WebFetch, or any other execution- or mutation-capable tool.
>
>   Cites decision `0003`. **Suggested resolution:** Revert `ALLOWED_TOOLS` to `"Read,Grep,Glob"`; running the target's tests was explicitly considered and rejected by decision 0003.

One comment per PR, edited in place on every push. When the violation is fixed, the comment and its label are **removed** — a clean PR gets silence, not an "all good" comment sitting around forever.

---

## Design

Six stages. Plain Python orchestration, no agent framework.

```
collect  →  select  →  review  →  verify  →  propose  →  report
```

| Stage | What it does |
|---|---|
| **collect** | Resolve base/head refs, compute the merge-base diff, list changed paths |
| **select** | Deterministic prefilter: match each decision's `scope` globs against changed files. No LLM, no tokens |
| **review** | One engine call with the selected decisions + diff → typed candidate findings |
| **verify** | A second, adversarial call that tries to **falsify** each candidate. Survivors only |
| **propose** | Detect significant architectural choices the diff makes that no decision covers, and draft records for them |
| **report** | Render markdown, upsert the sticky comment, set or clear the label |

### Three ideas the design rests on

**Precision over recall.** A tool that cries wolf gets muted once and never un-muted. Both prompts push hard toward dropping anything unconfirmed, and `verify` exists solely to kill false positives — it re-opens the cited decision file, checks the quote appears verbatim, confirms the scope genuinely covers the file, and considers whether the flagged code is already compliant a few lines away. Findings that don't survive are discarded silently.

**The engine never executes PR code.** The reviewer reads code written by whoever opened the PR. It runs with `--restricted --tools Read,Grep,Glob`, which strips Bash and every other execution- or mutation-capable tool and confines file access to the working directory. This is a security boundary, not tidiness — without it, a review bot is a remote code execution hole in a costume.

**Verbatim quotes, always.** Every finding must carry a sentence copied word-for-word from the decision it cites. This makes the verify stage able to check the decision actually says what the model claims, and makes a bad flag obvious to a human at a glance.

---

## Decision records

A decision is a markdown file in `decisions/`, named `NNNN-short-slug.md`:

```markdown
---
id: "0007"
title: All outbound HTTP goes through the shared client
status: accepted          # accepted | proposed | superseded
date: 2026-03-14
scope:                    # globs; omit or leave empty for repo-wide
  - "src/**/*.py"
tags: [http, networking]
---

## Context
Why this came up, and what alternative was rejected.

## Decision
All outbound HTTP MUST go through `src/http/client.py`. Direct use of
`requests`, `httpx`, or `urllib` outside that module is prohibited.

## Consequences
What this costs us.
```

**`scope` is load-bearing.** It drives a deterministic prefilter that runs before any LLM call, so a decision about HTTP clients is never even considered for a PR that only touches docs. Wrong globs mean a decision is either never checked or checked against everything.

**`status: superseded`** excludes a decision from review while preserving it in history. Never delete a decision — supersede it.

### What makes a decision worth writing

The corpus determines everything. A vague decision is a false-positive generator, and a few weak records will poison trust in the whole tool.

- **Name the rejected alternative.** If you can't name a plausible choice someone would have made instead, it isn't a decision — it's a description, and it will never be checkable.
- **Use MUST / MUST NOT / SHOULD**, phrased so a *diff* can be seen to violate it. The reviewer sees a decision body and a diff; a stated preference gives it nothing to check.
- **State a real cost.** A decision with no downside is a platitude.

Some real constraints deliberately *don't* belong here. Things whose correctness depends on context invisible in a diff, and anything a linter or type checker should catch, will generate noise rather than signal. `decision-agent` is not a substitute for `ruff`, `mypy`, or a test suite — it catches the architectural drift those tools are blind to.

---

## Setup

**Requirements:** Python ≥ 3.10, git, the [`gh`](https://cli.github.com) CLI, and the `claude` CLI.

```bash
pip install -e .
npm install -g @anthropic-ai/claude-code
```

### Credentials

The engine reads its credential from the environment only — never a flag, never config, so swapping it is a secret change rather than a code change. Either works:

- `CLAUDE_CODE_OAUTH_TOKEN` — from `claude setup-token`, using a Claude subscription
- `ANTHROPIC_API_KEY` — a console API key

A subscription token is tied to one person's account, so every review draws on *their* usage limits. Fine for building and dogfooding; move to an API key before a team relies on it.

### GitHub Action

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
        with:
          fetch-depth: 0        # required: merge-base needs full history
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -e .
      - run: npm install -g @anthropic-ai/claude-code
      - run: decision-agent review --explain
        env:
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

Set the secret with `gh secret set CLAUDE_CODE_OAUTH_TOKEN`.

---

## CLI

```bash
decision-agent review [OPTIONS]
```

| Flag | Purpose |
|---|---|
| `--base TEXT` | Base ref (default: `$GITHUB_BASE_REF`, else `main`) |
| `--head TEXT` | Head ref (default: `$GITHUB_HEAD_REF`, else `HEAD`) |
| `--repo-root DIR` | Repository to review (default: `.`) |
| `--config FILE` | Path to `.decision-agent.toml` |
| `--dry-run` | Print the comment to stdout; never touch GitHub. Still uses the real engine |
| `--stub` | No LLM calls, no credential needed. Independent of `--dry-run` |
| `--explain` | Print the pipeline's reasoning to **stderr** |
| `--explain-prompts` | Also dump full prompt text (verbose) |
| `--pr INTEGER` | PR number (default: from `GITHUB_REF` or `gh pr view`) |

`--dry-run` and `--stub` are deliberately separate: `--dry-run` alone is how you exercise the real reviewer locally without posting, and `--dry-run --stub` is the free, credential-free path for checking selection logic.

### Tuning with `--explain`

`--explain` writes to stderr so stdout stays pipeable, and answers the only question that matters when tuning: *why did it do that?*

```
3. SELECT
[SELECTED] 0001 (Decisions live in decisions/): repo-wide (no scope restriction)
[dropped ] 0002 (Three-dot merge-base diff): no scope glob matched any changed file
[SELECTED] 0003 (Engine runs read-only sandboxed): scope glob
           'src/decision_agent/engine.py' matched changed file
           'src/decision_agent/engine.py'

5. VERIFY
- [UPHELD ] decision 0003: src/decision_agent/engine.py (lines 30) -- Quote
  confirmed verbatim. Scope covers the file. The diff adds Bash with a comment
  admitting it bypasses the sandbox -- precisely the alternative the decision's
  Context says was considered and rejected.

7. THRESHOLD
severity_threshold='high' hid 1 upheld finding(s)
```

That last section matters: without it, "it found something and chose not to show you" is invisible.

---

## Configuration

`.decision-agent.toml` in the repo root, all keys optional:

```toml
decisions_dir       = "decisions"
model               = "sonnet"
severity_threshold  = "medium"      # below this, nothing is reported
max_diff_bytes      = 400_000       # larger diffs are chunked per file
propose_decisions   = true
label               = "decision-conflict"
ignore_paths        = ["**/*.lock", "dist/**", "**/*.min.js"]
```

`DECISION_AGENT_MODEL` and `DECISION_AGENT_SEVERITY_THRESHOLD` override the corresponding keys in CI.

`ignore_paths` removes matching files from the changed-file list **and strips their sections out of the diff**, so a multi-megabyte lockfile never reaches a prompt.

---

## Implementation notes

**Three-dot diffs.** `git diff base...head` means "what this branch added since it diverged." Two-dot drags in commits that landed on `base` afterward and produces phantom findings. Requires `fetch-depth: 0`.

**Ref resolution.** `GITHUB_HEAD_REF` is a bare branch name, and `actions/checkout` leaves a detached HEAD with no such local branch. Both refs are resolved as `<ref>` then `origin/<ref>`; the head additionally falls back to `HEAD`, since a fork PR's ref is absent from `origin` entirely and failing there would fail the job on every outside contribution.

**Structured output.** The engine passes a JSON Schema derived from the pydantic models via `claude -p --json-schema`, and validates the result. No prose is ever scraped.

**Sticky comments.** An HTML marker (`<!-- decision-agent:v1 -->`) in the body is matched on each run; the existing comment is edited rather than a new one posted. Comment listing uses `gh api --paginate --slurp`, because `--paginate` alone emits concatenated JSON arrays that aren't valid JSON past the first page.

**GitHub access** goes through the `gh` CLI, which is preinstalled on runners and picks up `GITHUB_TOKEN` automatically — no auth code, no API client dependency.

---

## Known limitations

**Fork PRs don't work.** GitHub withholds secrets from PRs opened from forks, so the credential is absent and the job fails. On a public repo this means outside contributions get a red ❌ from a bot that simply couldn't authenticate. Guard the job with:

```yaml
if: github.event.pull_request.head.repo.full_name == github.repository
```

**Silence is ambiguous without `--explain`.** No comment could mean "reviewed, found nothing" or "no decision's scope matched." Keep `--explain` on in CI.

**Coverage is exactly your scope globs.** A file in no decision's `scope` can never be flagged by anything. Nothing warns you about this.

**A closed PR receives no events.** Pushing to the branch of a closed PR silently runs nothing at all.

---

## Development

```bash
python -m pytest -q          # unit tests; no network, no LLM calls
python -m pytest -m live     # one real `claude -p` call, to catch CLI flag drift
```

The live test is excluded by default and needs a credential. Everything else runs offline against recorded fixtures with a mocked engine.

To iterate on real changes without posting anything:

```bash
decision-agent review --base main --dry-run --explain
```
