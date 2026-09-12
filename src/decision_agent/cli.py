"""Entry point: `decision-agent review`."""

from __future__ import annotations

from pathlib import Path

import click

from decision_agent.config import load_config
from decision_agent.engine import ClaudeEngine, EngineError, StubEngine
from decision_agent.explain import render_explain
from decision_agent.gitctx import GitError
from decision_agent.render import MARKER, render_comment
from decision_agent.review import run_pipeline


@click.group()
def main() -> None:
    """Decision-conformance review agent."""


@main.command()
@click.option("--base", default=None, help="Base ref to diff against (default: $GITHUB_BASE_REF or main).")
@click.option("--head", default=None, help="Head ref to diff (default: $GITHUB_HEAD_REF or HEAD).")
@click.option("--repo-root", default=".", type=click.Path(exists=True, file_okay=False), help="Path to the git repository being reviewed.")
@click.option("--config", "config_path", default=None, type=click.Path(exists=True, dir_okay=False), help="Path to .decision-agent.toml (default: <repo-root>/.decision-agent.toml).")
@click.option(
    "--dry-run",
    is_flag=True,
    help=(
        "Print the rendered comment to stdout instead of posting to GitHub; "
        "GitHub is never touched. Says nothing about which engine runs -- "
        "the real engine runs by default, so this is the way to exercise "
        "it without posting. Combine with --stub to also skip LLM calls."
    ),
)
@click.option(
    "--stub",
    is_flag=True,
    help=(
        "Use the no-op stub engine: no LLM calls, no credential required, "
        "findings/proposals are always empty. Independent of --dry-run -- "
        "on its own, the (empty) result still posts to GitHub, which is "
        "useful for exercising the posting path without spending tokens."
    ),
)
@click.option(
    "--explain",
    is_flag=True,
    help=(
        "Print the pipeline's internal reasoning (collect/select/review/"
        "verify/propose/threshold) to stderr. Works with --stub, since "
        "stdout/the comment body are unaffected."
    ),
)
@click.option(
    "--explain-prompts",
    is_flag=True,
    help="Like --explain, but also print the full prompt text sent to each stage (verbose; implies --explain).",
)
@click.option("--token", default=None, help="Engine credential, if you would rather not export an env var.")
@click.option("--pr", "pr_number", default=None, type=int, help="Pull request number (default: resolved from GITHUB_REF or `gh pr view`). Ignored with --dry-run.")
def review(
    base: str | None,
    head: str | None,
    repo_root: str,
    token: str | None,
    config_path: str | None,
    dry_run: bool,
    stub: bool,
    explain: bool,
    explain_prompts: bool,
    pr_number: int | None,
) -> None:
    """Review the diff between BASE and HEAD against recorded decisions."""
    root = Path(repo_root).resolve()
    cfg = load_config(repo_root=root, path=Path(config_path) if config_path else None)

    engine = StubEngine() if stub else ClaudeEngine(cfg, repo_root=root, token=token)

    try:
        result = run_pipeline(cfg, engine, root, base=base, head=head)
    except GitError as exc:
        raise click.ClickException(str(exc)) from exc
    except EngineError as exc:
        raise click.ClickException(str(exc)) from exc

    if explain or explain_prompts:
        # stderr, deliberately: stdout carries the comment body (see
        # below), and --dry-run output must stay pipeable into a file or
        # `gh pr comment` without this diagnostic text riding along.
        click.echo(render_explain(result, cfg, show_prompts=explain_prompts), err=True)

    comment_body = render_comment(result.findings, result.proposals, cfg)

    if dry_run:
        click.echo(comment_body or "(no comment: nothing to report — clean PR)")
        return

    from decision_agent.github import ensure_label, remove_comment, remove_label, resolve_pr_number

    try:
        resolved_pr = resolve_pr_number(pr_number)
        if comment_body:
            from decision_agent.github import upsert_comment

            upsert_comment(resolved_pr, comment_body, MARKER)
            ensure_label(resolved_pr, cfg.label)
        else:
            remove_comment(resolved_pr, MARKER)
            remove_label(resolved_pr, cfg.label)
    except Exception as exc:  # noqa: BLE001 - surface any GitHub/gh failure clearly
        raise click.ClickException(f"failed to report results to GitHub: {exc}") from exc


if __name__ == "__main__":
    main()
