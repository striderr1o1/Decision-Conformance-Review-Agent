"""Stage orchestration: collect -> select -> review -> verify -> propose.

`report` (rendering + posting) lives in render.py / github.py and is
invoked by the CLI, not here, so this module stays testable without a
GitHub token.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from decision_agent.config import Config
from decision_agent.decisions import Decision, filter_ignored_paths, load_decisions, select_decisions
from decision_agent.engine import Engine
from decision_agent.gitctx import DiffContext, get_diff_context
from decision_agent.schema import Finding, ProposedDecision, VerifiedFinding

DIFF_FILE_DELIMITER = "diff --git "


@dataclass
class PipelineResult:
    diff_context: DiffContext
    all_decisions: list[Decision]
    selected_decisions: list[Decision]
    findings: list[VerifiedFinding]
    proposals: list[ProposedDecision]


def format_decisions(decisions: list[Decision]) -> str:
    if not decisions:
        return "(no decisions apply)"
    blocks = []
    for d in decisions:
        blocks.append(f"### Decision {d.id}: {d.title}\nStatus: {d.status}\n\n{d.body}")
    return "\n\n---\n\n".join(blocks)


def chunk_diff(diff: str, max_bytes: int) -> list[str]:
    """Split a unified diff into per-file chunks, then greedily pack them
    into batches under `max_bytes` so no single engine call gets an
    unbounded diff. Falls back to a single chunk if the diff has no
    recognizable file boundaries (e.g. it's already small)."""
    if len(diff.encode("utf-8")) <= max_bytes:
        return [diff]

    parts = diff.split(DIFF_FILE_DELIMITER)
    header, *file_diffs = parts
    file_diffs = [DIFF_FILE_DELIMITER + f for f in file_diffs]
    if not file_diffs:
        return [diff]

    chunks: list[str] = []
    current = header
    for file_diff in file_diffs:
        if current and len((current + file_diff).encode("utf-8")) > max_bytes:
            chunks.append(current)
            current = file_diff
        else:
            current += file_diff
    if current.strip():
        chunks.append(current)
    return chunks or [diff]


def build_review_prompt(decisions: list[Decision], diff: str) -> str:
    return (
        "## Recorded decisions\n\n"
        f"{format_decisions(decisions)}\n\n"
        "## Pull request diff\n\n"
        f"```diff\n{diff}\n```\n"
    )


def build_verify_prompt(findings: list[Finding], decisions: list[Decision], diff: str) -> str:
    cited_ids = {f.decision_id for f in findings}
    cited_decisions = [d for d in decisions if d.id in cited_ids]
    findings_json = "\n\n".join(
        f"{i + 1}. {f.model_dump_json(indent=2)}" for i, f in enumerate(findings)
    )
    return (
        "## Candidate findings to verify\n\n"
        f"{findings_json}\n\n"
        "## Decisions cited above\n\n"
        f"{format_decisions(cited_decisions)}\n\n"
        "## Original pull request diff\n\n"
        f"```diff\n{diff}\n```\n"
    )


def build_propose_prompt(decisions: list[Decision], diff: str) -> str:
    return (
        "## Existing recorded decisions (for reference; do not duplicate)\n\n"
        f"{format_decisions(decisions)}\n\n"
        "## Pull request diff\n\n"
        f"```diff\n{diff}\n```\n"
    )


def run_review_stage(engine: Engine, decisions: list[Decision], diff_ctx: DiffContext, max_diff_bytes: int) -> list[Finding]:
    findings: list[Finding] = []
    for chunk in chunk_diff(diff_ctx.diff, max_diff_bytes):
        if not chunk.strip():
            continue
        prompt = build_review_prompt(decisions, chunk)
        findings.extend(engine.review(prompt).findings)
    return findings


def run_verify_stage(
    engine: Engine, findings: list[Finding], decisions: list[Decision], diff_ctx: DiffContext
) -> list[VerifiedFinding]:
    if not findings:
        return []
    prompt = build_verify_prompt(findings, decisions, diff_ctx.diff)
    return engine.verify(prompt).verified


def run_propose_stage(engine: Engine, decisions: list[Decision], diff_ctx: DiffContext) -> list[ProposedDecision]:
    prompt = build_propose_prompt(decisions, diff_ctx.diff)
    return engine.propose(prompt).proposals


def run_pipeline(
    config: Config,
    engine: Engine,
    repo_root: Path,
    base: str | None = None,
    head: str | None = None,
) -> PipelineResult:
    diff_ctx = get_diff_context(repo_root, base, head)
    all_decisions = load_decisions(config.decisions_path())
    relevant_files = filter_ignored_paths(diff_ctx.changed_files, config.ignore_paths)
    selected = select_decisions(all_decisions, relevant_files)

    findings: list[VerifiedFinding] = []
    proposals: list[ProposedDecision] = []

    has_diff = bool(diff_ctx.diff.strip())

    if selected and has_diff:
        candidates = run_review_stage(engine, selected, diff_ctx, config.max_diff_bytes)
        findings = run_verify_stage(engine, candidates, selected, diff_ctx)

    if config.propose_decisions and has_diff:
        proposals = run_propose_stage(engine, all_decisions, diff_ctx)

    return PipelineResult(
        diff_context=diff_ctx,
        all_decisions=all_decisions,
        selected_decisions=selected,
        findings=findings,
        proposals=proposals,
    )
