---
id: "0004"
title: The engine credential comes from the environment, never a CLI flag
status: accepted
date: 2026-09-12
scope:
  - "src/decision_agent/engine.py"
tags: [security, credentials]
---

## Context

This project chose headless Claude Code over the `anthropic` Python SDK
specifically because the credential needed to be swappable: a Claude
subscription's `claude setup-token` OAuth token now, an `ANTHROPIC_API_KEY`
later, with neither choice requiring a code change — only a different
secret wired into the same `env:` block in the GitHub Actions workflow.
The alternative actually available was accepting the credential as a CLI
argument to `decision-agent` (e.g. `--token`), which would have been more
convenient for a one-off local run where exporting an env var feels like
an extra step.

## Decision

`ClaudeEngine._run` (via `_check_credential` and `CREDENTIAL_ENV_VARS`)
MUST read the credential only from `os.environ`, checking
`CLAUDE_CODE_OAUTH_TOKEN` then `ANTHROPIC_API_KEY`. `decision-agent` MUST
NOT accept the credential as a CLI flag or positional argument, and the
`claude` subprocess command built in `engine.py` MUST NOT include the
credential as an explicit argument on the command line.

## Consequences

There is no way to override the credential for a single invocation without
mutating the process environment first — a quick "try it with a different
token" requires an `export` (or an inline `VAR=... decision-agent ...`),
not a flag. It also means a missing credential is only discovered when a
stage actually tries to call the engine (raising `MissingCredentialError`
at `_run` time), not at argument-parsing time when the CLI starts, so a
long `--dry-run` invocation can fail only after collect/select have
already run.
