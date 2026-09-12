---
id: "0005"
title: Engine output is parsed as schema-validated JSON, never scraped from prose
status: accepted
date: 2026-09-12
scope:
  - "src/decision_agent/engine.py"
  - "src/decision_agent/schema.py"
tags: [schema, engine]
---

## Context

Every stage's output (findings, verified findings, proposals) has to be
consumed by plain Python code in CI with nobody reading the model's
answer first. The alternative actually considered was having the model
answer in markdown/prose — closer to how a human reviewer writes a
comment — and extracting findings from that with regex or a lenient
markdown parser. That was rejected because prose extraction is brittle
against ordinary phrasing drift between model calls or model versions,
and a parsing failure there would be the single most common way for CI
to break.

## Decision

Every engine call MUST pass `--json-schema` built from the pydantic
models in `schema.py` (`REVIEW_SCHEMA`, `VERIFY_SCHEMA`, `PROPOSE_SCHEMA`,
via `json_schema_for`), and the result MUST be parsed by
`<Model>.model_validate()`-ing the extracted JSON into the corresponding
`ReviewResult` / `VerifyResult` / `ProposeResult`. `engine.py` MUST NOT
attempt to pull findings out of `result.stdout` by string or regex
matching. `extract_structured` MAY unwrap the `claude -p --output-format
json` envelope's `result` field whether it arrives as a JSON string or an
already-parsed object, but any failure to parse MUST be raised as
`EngineError` — it MUST NOT let a raw `json.JSONDecodeError` escape to the
caller.

## Consequences

The system is coupled to `claude -p` continuing to support
`--output-format json` together with `--json-schema`, and to the
envelope shape (`type`/`result`/...) staying as `extract_structured`
expects. A future CLI release that changes how structured output is
returned breaks review, verify, and propose all at once, in the same way,
rather than degrading gracefully one stage at a time.
