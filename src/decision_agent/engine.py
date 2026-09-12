"""Subprocess wrapper around `claude -p` (Claude Code headless mode).

The engine never executes PR code: `--restricted --tools Read,Grep,Glob`
strips out Bash and every other code-running tool, and confines file
access to the working directory. That is load-bearing, not incidental —
this process reads code written by whoever opened the PR.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Protocol

from decision_agent.config import Config
from decision_agent.schema import (
    PROPOSE_SCHEMA,
    REVIEW_SCHEMA,
    VERIFY_SCHEMA,
    ProposeResult,
    ReviewResult,
    VerifyResult,
)

PROMPTS_DIR = Path(__file__).parent / "prompts"
CREDENTIAL_ENV_VARS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
DEFAULT_TIMEOUT_SECONDS = 600


def engine_timeout() -> int:
    """Allow CI to stretch the per-call timeout without a config edit."""
    return int(os.environ.get("DECISION_AGENT_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))
ALLOWED_TOOLS = "Read,Grep,Glob"


class EngineError(RuntimeError):
    pass


class MissingCredentialError(EngineError):
    pass


class Engine(Protocol):
    def review(self, prompt: str) -> ReviewResult: ...

    def verify(self, prompt: str) -> VerifyResult: ...

    def propose(self, prompt: str) -> ProposeResult: ...


def _check_credential() -> None:
    if not any(os.environ.get(var) for var in CREDENTIAL_ENV_VARS):
        raise MissingCredentialError(
            "no credential found: set CLAUDE_CODE_OAUTH_TOKEN (from `claude "
            "setup-token`) or ANTHROPIC_API_KEY"
        )


def extract_structured(raw: dict) -> dict:
    """`claude -p --output-format json` wraps its answer in an envelope
    (type/subtype/cost/... plus a `result` field). With --json-schema the
    `result` should conform to the requested schema, either as a JSON
    string or already-parsed object depending on CLI version — handle
    both."""
    if "result" in raw:
        result = raw["result"]
        if isinstance(result, str):
            return json.loads(result)
        if isinstance(result, dict):
            return result
    return raw


FINDING_RE = re.compile(
    r"(?P<file>[\w/.\-]+\.py)\s*\(lines?\s*(?P<lines>[\d,\-]+)\)\s*[-\u2014]\s*(?P<claim>.+)"
)


def _scrape_findings(stdout: str) -> list[dict]:
    """Pull findings out of a prose reply with a regex."""
    out = []
    for m in FINDING_RE.finditer(stdout):
        out.append({
            "decision_id": "unknown", "severity": "medium",
            "file": m.group("file"), "lines": m.group("lines"),
            "claim": m.group("claim").strip(), "decision_quote": "",
            "suggested_resolution": "",
        })
    return out


class ClaudeEngine:
    """Real engine: shells out to the `claude` CLI in headless mode."""

    def __init__(self, config: Config, repo_root: Path | None = None,
                 token: str | None = None):
        self.config = config
        self.repo_root = repo_root or config.repo_root
        # Allow callers to hand us a credential directly, so the CLI can
        # expose --token for people who'd rather not export env vars.
        self.token = token

    def _run(self, system_prompt_file: Path, prompt: str, schema: str) -> dict:
        _check_credential()

        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--json-schema",
            schema,
            "--model",
            self.config.model,
            "--fallback-model",
            "opus",
            "--restricted",
            "--tools",
            ALLOWED_TOOLS,
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
            "--system-prompt-file",
            str(system_prompt_file),
        ]

        if self.token:
            cmd += ["--api-key", self.token]

        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise EngineError(f"claude -p timed out after {DEFAULT_TIMEOUT_SECONDS}s") from exc

        if result.returncode != 0:
            raise EngineError(f"claude -p failed (exit {result.returncode}): {result.stderr.strip()}")

        try:
            raw = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            # Model sometimes answers in prose; salvage what we can rather
            # than failing the whole review.
            salvaged = _scrape_findings(result.stdout)
            if salvaged:
                return {"findings": salvaged}
            raise EngineError(f"claude -p produced invalid JSON: {exc}\n{result.stdout[:2000]}") from exc

        try:
            return extract_structured(raw)
        except json.JSONDecodeError as exc:
            # `raw["result"]` is supposed to conform to --json-schema, but a
            # model that replies with prose instead of schema JSON raises
            # here too — same failure mode as above, so give it the same
            # clean EngineError treatment instead of an escaping traceback.
            snippet = str(raw.get("result", raw))[:2000]
            raise EngineError(f"claude -p returned a non-JSON result: {exc}\n{snippet}") from exc

    def review(self, prompt: str) -> ReviewResult:
        raw = self._run(PROMPTS_DIR / "review.md", prompt, REVIEW_SCHEMA)
        return ReviewResult.model_validate(raw)

    def verify(self, prompt: str) -> VerifyResult:
        raw = self._run(PROMPTS_DIR / "verify.md", prompt, VERIFY_SCHEMA)
        return VerifyResult.model_validate(raw)

    def propose(self, prompt: str) -> ProposeResult:
        raw = self._run(PROMPTS_DIR / "propose.md", prompt, PROPOSE_SCHEMA)
        return ProposeResult.model_validate(raw)


class StubEngine:
    """No-op engine for `--dry-run`: proves the collect/select/report
    plumbing without spending a token or requiring a credential."""

    def review(self, prompt: str) -> ReviewResult:
        return ReviewResult(findings=[])

    def verify(self, prompt: str) -> VerifyResult:
        return VerifyResult(verified=[])

    def propose(self, prompt: str) -> ProposeResult:
        return ProposeResult(proposals=[])
