# Prompting Module

Owner: `openminion-prompting`
Shape: `small-primitive`
Runtime peer: standalone shared module (services and modules may import it)

Purpose: own stable OpenMinion runtime prompt fragments and small render helpers that are shared across modules and services.

Reference: `docs/reference/openminion-prompt-ownership-reference.md`

## Owns

- default system, identity, safety, and tool-result prompt fragments shared by agent/context paths
- continuation and resume prompt fragments shared outside one domain state machine
- stable context block headings and simple render helpers
- brain decision prompt fragments whose wording is a shared runtime contract
- finalization-status guidance fragments shared by execution lanes
- session-summary labels that are reused by memory/session context renderers

## Does not own

- model-specific prompt tuning
- provider-specific prompt variants
- test/eval fixture prompts
- domain-specific evaluator, retry, tool-loop, or goal-loop policy prompts
- memory extraction or memory-promotion semantics
- a mutable prompt registry or string-key runtime lookup layer

Domain-specific prompt builders stay with their domain owner, preferably in a
local `prompts.py` module when there is more than one prompt or a non-trivial
renderer. Files intentionally kept outside this module must be listed in
`scripts.validate.prompt_literals` with a durable owner rationale.

## Public surface

`openminion.modules.prompting` re-exports every stable fragment and helper named
in this module. Callers may import from the package root when they need a shared
prompt contract, or from the leaf file when that makes the local owner clearer.

This package intentionally has no `cli.py`, `service.py`, `interfaces.py`,
`schemas.py`, or `config.py`. It does not expose runtime state, typed payloads,
operator-tunable settings, or a callable service boundary.
