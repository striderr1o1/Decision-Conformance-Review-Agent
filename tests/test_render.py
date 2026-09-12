"""Findings -> markdown comment rendering: grouping, thresholding, silence."""

from __future__ import annotations

from decision_agent.config import Config
from decision_agent.render import MARKER, render_comment
from decision_agent.schema import ProposedDecision, VerifiedFinding


def make_finding(**overrides) -> VerifiedFinding:
    defaults = dict(
        decision_id="0007",
        severity="high",
        file="src/service.py",
        lines="10-12",
        claim="calls requests.get directly",
        decision_quote="All outbound HTTP MUST go through `src/http/client.py`.",
        suggested_resolution="use src/http/client.py instead",
        upheld=True,
        verification_note="quote confirmed verbatim in decision 0007",
    )
    defaults.update(overrides)
    return VerifiedFinding(**defaults)


def make_config(**overrides) -> Config:
    return Config(**overrides)


def test_no_findings_no_proposals_returns_none() -> None:
    assert render_comment([], [], make_config()) is None


def test_finding_below_threshold_returns_none() -> None:
    finding = make_finding(severity="low")
    cfg = make_config(severity_threshold="medium")
    assert render_comment([finding], [], cfg) is None


def test_finding_not_upheld_is_excluded() -> None:
    finding = make_finding(upheld=False)
    cfg = make_config(severity_threshold="low")
    assert render_comment([finding], [], cfg) is None


def test_finding_at_threshold_renders_with_marker() -> None:
    finding = make_finding(severity="medium")
    cfg = make_config(severity_threshold="medium")
    body = render_comment([finding], [], cfg)

    assert body is not None
    assert body.startswith(MARKER)
    assert "0007" in body
    assert "src/service.py" in body
    assert finding.decision_quote in body
    assert finding.suggested_resolution in body


def test_findings_grouped_high_before_low() -> None:
    low = make_finding(severity="low", file="low.py")
    high = make_finding(severity="high", file="high.py")
    cfg = make_config(severity_threshold="low")

    body = render_comment([low, high], [], cfg)

    assert body is not None
    assert body.index("high.py") < body.index("low.py")


def test_proposals_rendered_in_collapsed_details() -> None:
    proposal = ProposedDecision(
        title="Adopt structured logging",
        rationale="diff introduces a new cross-cutting logging pattern",
        suggested_scope=["src/**/*.py"],
        draft_body="## Decision\nUse structlog everywhere.",
    )
    cfg = make_config()
    body = render_comment([], [proposal], cfg)

    assert body is not None
    assert "<details>" in body
    assert "Adopt structured logging" in body
    assert "structlog" in body


def test_proposals_suppressed_when_propose_decisions_false() -> None:
    proposal = ProposedDecision(
        title="Adopt structured logging",
        rationale="...",
        suggested_scope=[],
        draft_body="body",
    )
    cfg = make_config(propose_decisions=False)
    assert render_comment([], [proposal], cfg) is None
