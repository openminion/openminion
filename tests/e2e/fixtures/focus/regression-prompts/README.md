# Focus Regression Prompt Corpus

This folder owns reusable seed prompts for long-horizon Focus regression runs.
The prompts are deliberately short: they define the objective, boundaries, and
completion standard while leaving research, decomposition, tool selection, and
execution to the agent.

This is a fixture corpus for the existing Focus E2E system, not another smoke
runner. Use the current PTY harness and smoke levels documented in
[`tests/e2e/cli/focus/README.md`](../../../cli/focus/README.md). Store generated
transcripts, scratch workspaces, and run ledgers under `workspace-tmp/<lane>/`.

## Scenario Families

| File | IDs | Primary coverage |
| --- | --- | --- |
| [`research.md`](research.md) | `LH-RES-*` | retrieval, sources, contradiction handling, synthesis |
| [`planning.md`](planning.md) | `LH-PLAN-*` | objective retention, plans, progress, stop decisions |
| [`delegation.md`](delegation.md) | `LH-DEL-*` | decomposition, bounded handoff, review, reconciliation |
| [`coding.md`](coding.md) | `LH-CODE-*` | repository intake, implementation, debugging, verification |
| [`continuity.md`](continuity.md) | `LH-CONT-*` | memory, context, pause, restart, and recovery |
| [`personal-assistant.md`](personal-assistant.md) | `LH-ASST-*` | daily planning, travel, personal administration |
| [`mixed-workflows.md`](mixed-workflows.md) | `LH-MIX-*` | research-to-code and incident-style cross-tool work |

## Run Contract

1. Record the exact Git SHA, model, profile, scenario ID and version, start and
   end time, permission profile, and evidence root.
2. Run the prompt unchanged. Add only fixture paths, dates, locations, or other
   values named by its prerequisites.
3. Capture the full PTY transcript plus any generated files, task state,
   memory evidence, tool traces, verifier output, and terminal result.
4. Check every listed pass-evidence item. A fluent answer is not a pass when a
   required artifact, source, delegated result, restart, or verifier is absent.
5. Assign one disposition: `pass`, `real_regression`, `provider_external`,
   `environment`, `policy_blocked`, or `not_applicable`.
6. Rerun a suspected transient failure once. Repeated failure with the same
   cause must be investigated before changing the prompt or expectation.

## Result Record

Use this shape in the campaign ledger stored under `workspace-tmp`:

```text
scenario_id:
scenario_version:
git_sha:
model_profile:
started_at:
finished_at:
disposition:
evidence_root:
passed_evidence:
failed_evidence:
failure_owner:
notes:
```

## Corpus Rules

1. Keep scenario IDs stable after a baseline has been recorded.
2. Increment `Version` when the prompt, prerequisites, or pass evidence changes.
3. Add a new scenario for a materially different objective instead of turning
   an existing scenario into a broad catch-all.
4. Do not weaken evidence requirements, add phrase-specific expectations, or
   hide a product failure behind a skip.
5. Keep prompts model-neutral. Provider-specific setup belongs in the run
   record, not the prompt.
6. Keep external effects bounded. Personal-assistant scenarios are read-only
   unless a separate test explicitly supplies approval for a mutation.
7. Prefer two or three durable scenarios per family. Add more only when a new
   user journey or regression class is not represented.

## Suggested Campaigns

- Fast breadth: `LH-RES-001`, `LH-PLAN-001`, `LH-DEL-001`, `LH-CODE-001`,
  `LH-CONT-001`, `LH-ASST-001`, and `LH-MIX-001`.
- Long-horizon continuity: `LH-PLAN-002`, `LH-CODE-003`, `LH-CONT-001`, and
  `LH-CONT-002` with an intentional process restart.
- Full live capability: run every applicable scenario and classify unavailable
  integrations honestly rather than treating them as passes.
