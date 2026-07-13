# Linux Operations Diagnostics

## Purpose

Collect bounded, read-only evidence about a configured local, container, or SSH
target without accepting model-authored shell commands.

## Metadata

1. `skill_id`: `ops-linux-diagnostics`
2. `capability_pack`: `ops-linux-readonly`
3. `capability_domain`: `system_operations`
4. `requires_tools`: `ops.target.list`, `ops.target.inspect`,
   `ops.host.snapshot`, `ops.service.inspect`, `ops.logs.query`,
   `ops.network.inspect`, `ops.command.observe`
5. `forbidden_claims`: successful remediation without post-change evidence
6. `evidence_expectations`: cite target and evidence identifiers

## Procedure

1. List targets and select one explicit target identifier.
2. Inspect the target before requesting observations.
3. Choose the smallest closed observation profile that answers the question.
4. Correlate typed evidence by target, profile, timestamp, and claim status.
5. Report observed facts separately from hypotheses and unavailable evidence.

## Stop Conditions

1. Stop when the requested claim is supported by bounded evidence.
2. Stop and report a blocked claim when the target, policy, transport, or
   credential is unavailable.
3. Do not invent commands, mutate a target, or claim a repair from read-only
   evidence.
