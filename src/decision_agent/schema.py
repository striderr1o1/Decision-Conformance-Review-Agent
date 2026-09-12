"""Typed shapes for engine output, and JSON Schema export for `--json-schema`."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["high", "medium", "low"]


class Finding(BaseModel):
    """A candidate violation of a recorded decision, found in the diff."""

    decision_id: str = Field(description="id of the decision this finding cites")
    severity: Severity
    file: str = Field(description="path of the file containing the violation")
    lines: str = Field(description='line range in the new file, e.g. "42-58"')
    claim: str = Field(description="what the diff does that conflicts with the decision")
    decision_quote: str = Field(
        description="verbatim sentence copied from the decision file supporting this claim"
    )
    suggested_resolution: str = Field(description="a concrete way to resolve the conflict")


class ReviewResult(BaseModel):
    """Top-level output of the review stage."""

    findings: list[Finding] = Field(default_factory=list)


class VerifiedFinding(Finding):
    """A finding after the verify (falsification) pass."""

    upheld: bool = Field(description="false if the verify pass could not substantiate this finding")
    verification_note: str = Field(
        default="", description="why the finding was upheld or dropped"
    )


class VerifyResult(BaseModel):
    verified: list[VerifiedFinding] = Field(default_factory=list)


class ProposedDecision(BaseModel):
    """A draft decision record for a significant undocumented choice in the diff."""

    title: str
    rationale: str = Field(description="why this looks like a decision worth recording")
    suggested_scope: list[str] = Field(default_factory=list)
    draft_body: str = Field(description="a full markdown decision body, in the project's format")


class ProposeResult(BaseModel):
    proposals: list[ProposedDecision] = Field(default_factory=list)


def json_schema_for(model: type[BaseModel]) -> str:
    """Render a pydantic model's JSON Schema as a compact string, suitable
    for passing to `claude -p --json-schema`."""
    return json.dumps(model.model_json_schema())


REVIEW_SCHEMA = json_schema_for(ReviewResult)
VERIFY_SCHEMA = json_schema_for(VerifyResult)
PROPOSE_SCHEMA = json_schema_for(ProposeResult)
