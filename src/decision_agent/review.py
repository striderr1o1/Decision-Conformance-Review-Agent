"""Stage orchestration: collect -> select -> review -> verify -> propose.

`report` (rendering + posting) lives in render.py / github.py and is
invoked by the CLI, not here, so this module stays testable without a
GitHub token.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from decision_agent.config import Config
from decision_agent.decisions import Decision, filter_ignored_paths, load_decisions, select_decisions
from decision_agent.engine import Engine
from decision_agent.gitctx import DiffContext, get_diff_context
from decision_agent.globmatch import match, match_any
from decision_agent.schema import Finding, ProposedDecision, VerifiedFinding

DIFF_FILE_DELIMITER = "diff --git "


@dataclass
class IgnoredPath:
    """One changed path `filter_ignored_paths` dropped, and the specific
    `ignore_paths` glob that matched it. Explain-only bookkeeping -- it has
    no effect on which files actually get filtered."""

    path: str
    matched_pattern: str


@dataclass
class SelectionExplanation:
    """Why one decision was or wasn't selected, for every decision (not
    just the selected ones). Mirrors `select_decisions`'s own logic
    (status check first, then scope) so the reason can never disagree with
    what actually happened, but keeps the specific glob/file a bool can't
    express."""

    decision_id: str
    title: str
    selected: bool
    reason: str


@dataclass
class PipelineResult:
    diff_context: DiffContext
    all_decisions: list[Decision]
    selected_decisions: list[Decision]
    findings: list[VerifiedFinding]
    proposals: list[ProposedDecision]
    # Everything below exists for `--explain`. Always populated (never
    # optional) -- it's cheap to collect since the data already exists in
    # memory mid-pipeline, and a conditional field would mean two code
    # paths to keep in sync.
    dropped_paths: list[IgnoredPath] = field(default_factory=list)
    selection_explanations: list[SelectionExplanation] = field(default_factory=list)
    review_candidates: list[Finding] = field(default_factory=list)
    diff_chunk_count: int = 0
    review_prompts: list[str] = field(default_factory=list)
    verify_prompt: str | None = None
    propose_prompt: str | None = None


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


def _diff_file_path(file_diff: str) -> str | None:
    """Pull the path out of one `diff --git ...` section. The header line
    itself (`diff --git a/<path> b/<path>`) is ambiguous when a path
    contains a space — git doesn't quote spaces, so there's no reliable
    place to split it. The `+++ b/<path>` / `--- a/<path>` lines don't have
    that problem (one path per line), so prefer those; fall back to the
    header only for sections that have neither (pure mode changes, some
    binary diffs).

    Only the lines *before* the first hunk (`@@ ...`) are metadata — an
    added line that itself happens to start with `++` (or a removed line
    starting with `--`) renders as `+++ .../--- ...` too, and must not be
    mistaken for the real header."""
    plus_path = minus_path = None
    for line in file_diff.splitlines():
        if line.startswith("@@"):
            break
        if line.startswith("+++ "):
            plus_path = line[len("+++ ") :]
        elif line.startswith("--- "):
            minus_path = line[len("--- ") :]

    for candidate in (plus_path, minus_path):
        if candidate and candidate != "/dev/null":
            return candidate[2:] if candidate[:2] in ("a/", "b/") else candidate

    header = file_diff.splitlines()[0] if file_diff else ""
    if header.startswith(DIFF_FILE_DELIMITER):
        rest = header[len(DIFF_FILE_DELIMITER) :]
        marker = rest.rfind(" b/")
        if marker != -1:
            return rest[marker + len(" b/") :]
    return None


def strip_ignored_files(diff: str, ignore_patterns: list[str]) -> str:
    """Remove whole per-file sections for paths matching `ignore_patterns`
    before the diff reaches any prompt builder. Filtering the changed-file
    *list* used for scope selection isn't enough on its own — the ignored
    file's full contents would still ride along in the diff, burning context
    and inviting findings on a file the user declared irrelevant.

    Splits on `DIFF_FILE_DELIMITER` the same way `chunk_diff` does, rather
    than adding a second diff parser."""
    if not ignore_patterns:
        return diff

    header, *file_diffs = diff.split(DIFF_FILE_DELIMITER)
    kept = []
    for file_diff in file_diffs:
        section = DIFF_FILE_DELIMITER + file_diff
        path = _diff_file_path(section)
        if path is not None and match_any(ignore_patterns, path):
            continue
        kept.append(section)
    return header + "".join(kept)


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


@dataclass
class ReviewStageResult:
    candidates: list[Finding]
    prompts: list[str]
    chunk_count: int


@dataclass
class VerifyStageResult:
    verified: list[VerifiedFinding]
    prompt: str | None


@dataclass
class ProposeStageResult:
    proposals: list[ProposedDecision]
    prompt: str


def run_review_stage(
    engine: Engine, decisions: list[Decision], diff_ctx: DiffContext, max_diff_bytes: int
) -> ReviewStageResult:
    chunks = [c for c in chunk_diff(diff_ctx.diff, max_diff_bytes) if c.strip()]
    findings: list[Finding] = []
    prompts: list[str] = []
    for chunk in chunks:
        prompt = build_review_prompt(decisions, chunk)
        prompts.append(prompt)
        findings.extend(engine.review(prompt).findings)
    return ReviewStageResult(candidates=findings, prompts=prompts, chunk_count=len(chunks))


def run_verify_stage(
    engine: Engine, findings: list[Finding], decisions: list[Decision], diff_ctx: DiffContext
) -> VerifyStageResult:
    if not findings:
        return VerifyStageResult(verified=[], prompt=None)
    prompt = build_verify_prompt(findings, decisions, diff_ctx.diff)
    return VerifyStageResult(verified=engine.verify(prompt).verified, prompt=prompt)


def run_propose_stage(engine: Engine, decisions: list[Decision], diff_ctx: DiffContext) -> ProposeStageResult:
    prompt = build_propose_prompt(decisions, diff_ctx.diff)
    return ProposeStageResult(proposals=engine.propose(prompt).proposals, prompt=prompt)


def explain_ignored_paths(paths: list[str], ignore_patterns: list[str]) -> list[IgnoredPath]:
    """Like `filter_ignored_paths`, but keeps the dropped paths (and which
    glob dropped each one) instead of discarding them. Kept separate from
    `filter_ignored_paths` so that function's contract -- used for the
    actual filtering -- doesn't have to change shape for an explain-only
    need."""
    if not ignore_patterns:
        return []
    dropped = []
    for path in paths:
        for pattern in ignore_patterns:
            if match(pattern, path):
                dropped.append(IgnoredPath(path=path, matched_pattern=pattern))
                break
    return dropped


def explain_selection(decision: Decision, changed_files: list[str]) -> SelectionExplanation:
    """Same precedence as `Decision.is_active()`/`applies_to()` (status
    first, then scope), but reports which specific glob matched which
    specific file instead of collapsing that to a bool."""
    if not decision.is_active():
        return SelectionExplanation(
            decision_id=decision.id,
            title=decision.title,
            selected=False,
            reason=f"dropped: status {decision.status!r} is not active",
        )
    if not decision.scope:
        return SelectionExplanation(
            decision_id=decision.id,
            title=decision.title,
            selected=True,
            reason="selected: repo-wide (no scope restriction)",
        )
    for glob in decision.scope:
        for changed_file in changed_files:
            if match(glob, changed_file):
                return SelectionExplanation(
                    decision_id=decision.id,
                    title=decision.title,
                    selected=True,
                    reason=f"selected: scope glob {glob!r} matched changed file {changed_file!r}",
                )
    return SelectionExplanation(
        decision_id=decision.id,
        title=decision.title,
        selected=False,
        reason="dropped: no scope glob matched any changed file",
    )


def run_pipeline(
    config: Config,
    engine: Engine,
    repo_root: Path,
    base: str | None = None,
    head: str | None = None,
) -> PipelineResult:
    diff_ctx = get_diff_context(repo_root, base, head)
    all_decisions = load_decisions(config.decisions_path())

    # Explain bookkeeping for the ignore filter: computed from the
    # pre-filter file list so the dropped paths are still around to report.
    dropped_paths = explain_ignored_paths(diff_ctx.changed_files, config.ignore_paths)
    relevant_files = filter_ignored_paths(diff_ctx.changed_files, config.ignore_paths)
    # Keep changed_files and diff in lockstep: both drop the same ignored
    # files, so nothing downstream (scope selection, chunking, prompts) can
    # see one without the other.
    diff_ctx = replace(
        diff_ctx,
        changed_files=relevant_files,
        diff=strip_ignored_files(diff_ctx.diff, config.ignore_paths),
    )
    selected = select_decisions(all_decisions, relevant_files)
    selection_explanations = [explain_selection(d, relevant_files) for d in all_decisions]

    findings: list[VerifiedFinding] = []
    proposals: list[ProposedDecision] = []
    review_candidates: list[Finding] = []
    diff_chunk_count = 0
    review_prompts: list[str] = []
    verify_prompt: str | None = None
    propose_prompt: str | None = None

    has_diff = bool(diff_ctx.diff.strip())

    if selected and has_diff:
        review_result = run_review_stage(engine, selected, diff_ctx, config.max_diff_bytes)
        review_candidates = review_result.candidates
        review_prompts = review_result.prompts
        diff_chunk_count = review_result.chunk_count

        verify_result = run_verify_stage(engine, review_candidates, selected, diff_ctx)
        findings = verify_result.verified
        verify_prompt = verify_result.prompt

    if config.propose_decisions and has_diff:
        propose_result = run_propose_stage(engine, all_decisions, diff_ctx)
        proposals = propose_result.proposals
        propose_prompt = propose_result.prompt

    return PipelineResult(
        diff_context=diff_ctx,
        all_decisions=all_decisions,
        selected_decisions=selected,
        findings=findings,
        proposals=proposals,
        dropped_paths=dropped_paths,
        selection_explanations=selection_explanations,
        review_candidates=review_candidates,
        diff_chunk_count=diff_chunk_count,
        review_prompts=review_prompts,
        verify_prompt=verify_prompt,
        propose_prompt=propose_prompt,
    )
