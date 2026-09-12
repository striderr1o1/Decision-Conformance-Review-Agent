"""Render the pipeline's internal reasoning for `--explain` / `--explain-prompts`.

Written to stderr by the CLI, never stdout: stdout carries the rendered
comment body (see render.py), and `--dry-run` output must stay pipeable
into `gh pr comment` or a file without diagnostic noise riding along. This
module only builds the text; the CLI decides which stream it goes to.

This never touches `os.environ` or any credential. Stage prompts are
reproduced verbatim under `--explain-prompts`, but those prompts are built
from decisions + diff text only (see `review.build_*_prompt`) -- they never
contain a credential to begin with, so there is nothing to redact.
"""

from __future__ import annotations

from decision_agent.config import Config
from decision_agent.review import PipelineResult

RULE = "-" * 60


def _section(title: str) -> str:
    return f"\n{RULE}\n{title}\n{RULE}"


def render_explain(result: PipelineResult, config: Config, show_prompts: bool = False) -> str:
    ctx = result.diff_context
    lines: list[str] = []

    # 1. Collect
    lines.append(_section("1. COLLECT"))
    lines.append(f"base:          {ctx.base}")
    head_line = f"head:          {ctx.head}"
    if ctx.head_fell_back:
        head_line += "  (fell back to HEAD: requested head ref did not resolve locally or as origin/<ref>)"
    lines.append(head_line)
    lines.append(f"merge-base:    {ctx.merge_base}")
    total_changed = len(ctx.changed_files) + len(result.dropped_paths)
    lines.append(f"changed files: {total_changed} total, {len(ctx.changed_files)} after ignore filter")
    lines.append(f"diff size:     {ctx.diff_bytes} bytes")

    # 2. Ignore filter
    lines.append(_section("2. IGNORE FILTER"))
    if not result.dropped_paths:
        lines.append("(nothing dropped)")
    else:
        for dropped in result.dropped_paths:
            lines.append(f"- {dropped.path}  (matched ignore_paths glob {dropped.matched_pattern!r})")

    # 3. Select
    lines.append(_section("3. SELECT"))
    if not result.selection_explanations:
        lines.append("(no decisions found in decisions_dir)")
    else:
        for sel in result.selection_explanations:
            mark = "SELECTED" if sel.selected else "dropped "
            lines.append(f"[{mark}] {sel.decision_id} ({sel.title}): {sel.reason}")

    # 4. Review
    lines.append(_section("4. REVIEW"))
    if not result.selected_decisions:
        lines.append("(skipped: no decision selected for this diff)")
    elif result.diff_chunk_count == 0:
        lines.append("(skipped: diff is empty after the ignore filter)")
    else:
        # chunk_count alone is not reliable evidence the diff "fit" --
        # chunk_diff splits on file boundaries, so a diff that touches only
        # one file still comes back as a single chunk even when that one
        # file's diff is itself bigger than max_diff_bytes (nothing to
        # split it on). Compare diff_bytes to max_diff_bytes directly.
        oversized = ctx.diff_bytes > config.max_diff_bytes
        if not oversized:
            lines.append(f"diff fits in max_diff_bytes ({config.max_diff_bytes} bytes) -> 1 chunk sent")
        elif result.diff_chunk_count > 1:
            lines.append(
                f"diff ({ctx.diff_bytes} bytes) exceeds max_diff_bytes "
                f"({config.max_diff_bytes} bytes) -> split into {result.diff_chunk_count} chunks"
            )
        else:
            lines.append(
                f"diff ({ctx.diff_bytes} bytes) exceeds max_diff_bytes "
                f"({config.max_diff_bytes} bytes) but only touches one file section, "
                "so it could not be split further -> sent as 1 oversized chunk"
            )
        if not result.review_candidates:
            lines.append("no candidate findings")
        else:
            lines.append(f"{len(result.review_candidates)} candidate finding(s):")
            for f in result.review_candidates:
                lines.append(f"- [{f.severity}] decision {f.decision_id}: {f.file} (lines {f.lines})")

    # 5. Verify -- the main precision lever, so this is the most detailed section.
    lines.append(_section("5. VERIFY"))
    if not result.review_candidates:
        lines.append("(skipped: no candidate findings to verify)")
    elif not result.findings:
        lines.append("verify stage returned no findings for the candidates above")
    else:
        for f in result.findings:
            status = "UPHELD " if f.upheld else "dropped"
            note = f.verification_note or "(no verification_note given)"
            lines.append(f"- [{status}] decision {f.decision_id}: {f.file} (lines {f.lines}) -- {note}")

    # 6. Propose
    lines.append(_section("6. PROPOSE"))
    if not result.proposals:
        lines.append("(none)")
    else:
        lines.append(f"{len(result.proposals)} proposal(s):")
        for p in result.proposals:
            lines.append(f"- {p.title}")

    # 7. Threshold -- findings the verify stage upheld but that never made
    # it into the rendered comment because severity_threshold hid them.
    lines.append(_section("7. THRESHOLD"))
    hidden = [f for f in result.findings if f.upheld and not config.meets_threshold(f.severity)]
    if not hidden:
        lines.append(f"(nothing hidden; severity_threshold={config.severity_threshold!r})")
    else:
        lines.append(
            f"severity_threshold={config.severity_threshold!r} hid {len(hidden)} "
            "upheld finding(s) that would otherwise have been reported:"
        )
        for f in hidden:
            lines.append(f"- [{f.severity}] decision {f.decision_id}: {f.file} (lines {f.lines})")

    if show_prompts:
        lines.append(_section("PROMPTS"))
        if not result.review_prompts:
            lines.append("review prompt(s): (not sent)")
        else:
            for i, prompt in enumerate(result.review_prompts, start=1):
                lines.append(f"--- review prompt {i}/{len(result.review_prompts)} ---")
                lines.append(prompt)
        lines.append("--- verify prompt ---")
        lines.append(result.verify_prompt or "(not sent)")
        lines.append("--- propose prompt ---")
        lines.append(result.propose_prompt or "(not sent)")

    return "\n".join(lines) + "\n"
