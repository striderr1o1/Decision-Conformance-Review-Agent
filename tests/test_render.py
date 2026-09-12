"""Findings -> markdown comment rendering: grouping, thresholding, silence."""

from __future__ import annotations

from decision_agent.config import Config
from decision_agent.render import MARKER, _render_finding, render_comment
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


def test_multiline_decision_quote_every_line_blockquoted() -> None:
    # decision_quote is copied verbatim from hard-wrapped decision files, so
    # it is routinely multi-line. Every line must carry the "  > " prefix or
    # the blockquote (and the enclosing list item) breaks after line one.
    finding = make_finding(decision_quote="Line one\nline two\nline three.")
    cfg = make_config(severity_threshold="low")
    body = render_comment([finding], [], cfg)

    assert body is not None
    for line in ("Line one", "line two", "line three."):
        assert f"  > {line}" in body


def test_multiline_claim_continuation_lines_indented() -> None:
    # claim is model output and may contain newlines; a continuation line
    # without the 2-space list indent breaks out of the list item.
    finding = make_finding(claim="first line of claim\nsecond line of claim")
    cfg = make_config(severity_threshold="low")
    body = render_comment([finding], [], cfg)

    assert body is not None
    assert "— first line of claim" in body
    assert "  second line of claim" in body


def test_multiline_suggested_resolution_continuation_lines_indented() -> None:
    finding = make_finding(
        suggested_resolution="first line of resolution\nsecond line of resolution"
    )
    cfg = make_config(severity_threshold="low")
    body = render_comment([finding], [], cfg)

    assert body is not None
    assert "**Suggested resolution:** first line of resolution" in body
    assert "  second line of resolution" in body


def test_quote_with_blank_line_stays_one_blockquote() -> None:
    # A blank line inside the quote must render as a bare ">" so the
    # blockquote continues, rather than as an empty line that would end it.
    finding = make_finding(decision_quote="Paragraph one.\n\nParagraph two.")
    cfg = make_config(severity_threshold="low")
    body = render_comment([finding], [], cfg)

    assert body is not None
    assert "  >\n" in body
    # no bare empty line sitting between the two blockquoted paragraphs
    assert "\n\n" not in body.split("Paragraph one.")[1].split("Paragraph two.")[0]


def test_single_line_quote_unchanged_no_trailing_whitespace() -> None:
    # Single-line quote/claim/resolution behavior must be unchanged by the
    # new helpers, and the new per-line prefixing must not itself introduce
    # trailing whitespace on the quote line (the pre-existing "  " spacer
    # line between the quote and "Cites decision" is unrelated and untouched).
    finding = make_finding(
        decision_quote="All outbound HTTP MUST go through `src/http/client.py`.",
        claim="calls requests.get directly",
        suggested_resolution="use src/http/client.py instead",
    )
    rendered = _render_finding(finding)
    rendered_lines = rendered.splitlines()

    assert rendered_lines[0] == "- **src/service.py** (lines 10-12) — calls requests.get directly"
    assert rendered_lines[1] == "  > All outbound HTTP MUST go through `src/http/client.py`."
    assert rendered_lines[1] == rendered_lines[1].rstrip()
    assert rendered_lines[3] == (
        "  Cites decision `0007`. **Suggested resolution:** use src/http/client.py instead"
    )
