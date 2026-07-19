# Operations Incident Handoff

## Purpose

Produce a concise incident handoff from system-operations evidence and durable
job state.

## Metadata

1. `skill_id`: `ops-incident-handoff`
2. `capability_pack`: `ops-linux-readonly`
3. `capability_domain`: `system_operations`
4. `requires_tools`: all tools in `ops-linux-readonly`
5. `forbidden_claims`: unsupported root cause or remediation success
6. `evidence_expectations`: include target, job, and evidence identifiers

## Procedure

1. Inspect the target and relevant operation jobs.
2. Collect only the evidence profiles needed to explain current impact.
3. Separate observed facts, bounded inferences, unknowns, and attempted actions.
4. Include cancelled, timed-out, denied, or incomplete operations explicitly.
5. End with the safest next operator action and the evidence needed to verify it.

## Stop Conditions

1. Stop when another operator can reproduce the evidence trail.
2. Stop when missing access or evidence prevents a stronger conclusion.
3. Never hide failed observations or convert hypotheses into facts.
