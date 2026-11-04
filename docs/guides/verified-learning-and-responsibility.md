# Verified learning and ongoing responsibility

Status: implemented in source; live learning qualification blocked
Last updated: 2026-11-04

OpenMinion can retain a reviewed procedure from an agent's completed work,
evaluate its later usefulness, and admit an exact version for that agent.
Recurring security assessments use the existing finite task schedule. Learning
does not activate itself, and a scheduled assessment does not authorize repair.

## Author a procedure from retained work

Enable `skill.runtime_workflow_observation_enabled` in the runtime configuration
and use the general adaptive profile for development work. The observation
owner records successful substantive tool use on confirmed completion. Coding,
seeded replay, and direct-tool turns do not supply this observation evidence.
Use a local coding permission profile that allows file/code inspection and
editing alongside `exec.run`. Normal policy can direct read-only shell commands
to structured tools, so an exec-only allowlist can prevent acquisition.
Check the selected agent's stored evidence:

```sh
skillctl --config SKILL_CONFIG learning-observation-list --agent-id AGENT
skillctl --config SKILL_CONFIG learning-shape-list --agent-id AGENT
```

Use a stored `authoring_ready` shape with two distinct successful observations.
Ask the model to author a `SkillProposalDraft` JSON file from the development
evidence. It supplies the name, description, applicability, and procedure;
runtime does not infer procedure steps from tool names. For example:

```json
{
  "name": "local-regression-repair",
  "short_description": "Repair a reproduced local defect and verify its regression.",
  "risk_class": "medium",
  "tools": ["exec.run"],
  "applies_to": {"intents": ["modify"], "steps": ["act"]},
  "procedure": {
    "when_to_use": ["A local code defect has a reproducible failing example."],
    "do_not_use": ["Do not use for remote service changes."],
    "steps": ["Find the owner of the defect and make the focused repair."],
    "validation": ["Run the failing example and the relevant regression tests."]
  }
}
```

The risk must equal the stored shape's risk, and the tools must be a subset of
its observed tools. Procedure content does not grant permissions. Every
procedure field requires a nonempty list of nonempty strings.

```sh
skillctl --config SKILL_CONFIG learning-propose \
  --agent-id AGENT --shape-id SHAPE --procedure-file DRAFT.json
skillctl --config SKILL_CONFIG proposal-inspect PROPOSAL
skillctl --config SKILL_CONFIG proposal-review PROPOSAL \
  --reviewer-id REVIEWER \
  --criterion 'procedure:accepted:Reviewed applicability, steps, and validation.'
```

Replace uppercase placeholders with the returned identities and your paths.
Keep the same runtime data root throughout this lifecycle. The reviewer must
be independent of the creating agent. Repeating identical agent/shape/content
returns the same proposal. Changed content needs a new review and proof, even
when the name stays the same. The resulting skill is scoped to its creating
agent. The legacy `--shape-json` form remains metadata-only and cannot be mixed
with these authored inputs.

## Evaluate before applying

An operator-controlled evaluator prepares the reviewed package without
changing the normal catalog. It stages and explicitly admits that exact package
in a disposable evaluation store, runs development replay, and evaluates
held-out cases. Normal `learning-apply-proved` cannot bootstrap evaluation:
it requires retained successful replay proof first.

The bounded transfer design uses two applicable cases and one inapplicable
case, two repeats, and baseline/candidate arms: twelve fresh sessions. Freeze
the cases, independent external oracle, model/configuration/permissions,
candidate package, and acceptance rule before scoring. The oracle stays out of
the agent's workspace and prompts; this is a trusted-host experiment, not an
isolation guarantee. Retain failed attempts and nonselection. Do not force
candidate selection or rerun until a favorable result appears.

Each turn has ceilings of 40,000 canonical tokens, 40 tool calls, and 300
seconds. At most three observation-acquisition turns, one authoring turn, and
three development replays bring the complete attempt to at most nineteen turns,
760,000 tokens, and 5,700 summed seconds. Existing suitable stored observations
do not require invented acquisition turns.

Replay success and measured benefit are separate facts. Benefit requires an
additional externally correct applicable result, actual selection of the exact
candidate in a successful applicable trial, no paired regression, correct
inapplicable handling without selecting the candidate, no added interventions,
and aggregate candidate tokens at most 125% of baseline. Missing usage or
incomplete evidence cannot establish benefit. A retained report may honestly
say `no_demonstrated_gain`, `regression`, or `inconclusive`.

The initial live acquisition stopped before candidate authoring, and complete
usage could not be recovered from its retained evidence. No live learning benefit
has been established. Future runs require a reviewed frozen experiment and
complete retained usage for every model call, reconciled with physical call counts.

Record the evaluator's result artifact. When it contains a measurement report,
also supply the independently frozen manifest reference:

```sh
skillctl --config SKILL_CONFIG learning-replay-proof \
  --proposal-id PROPOSAL --result-ref EVALUATOR_RESULT \
  --measurement-manifest-ref MANIFEST
skillctl --config SKILL_CONFIG proposal-inspect PROPOSAL
skillctl --config SKILL_CONFIG learning-apply-proved --proposal-id PROPOSAL
```

For a replay result without a measurement report, omit
`--measurement-manifest-ref`. This does not establish a benefit claim. The
inspection result reports the measurement reference and verdict separately
from replay and application. Invalid attachments leave retained proof intact.

## Admit or roll back an exact version

Application creates a pending package; it does not make the skill selectable.
Use the returned skill ID and version hash for separate operator admission:

```sh
skillctl --config SKILL_CONFIG admit \
  --skill-id SKILL --version-hash VERSION \
  --expected-active-version-hash none --target-status verified \
  --reason 'Reviewed procedure and retained replay evidence'
```

Use `none` only when that skill has no active version. After replacing an
admitted version of the same skill, restore a previously admitted version with:

```sh
skillctl --config SKILL_CONFIG rollback \
  --skill-id SKILL --to-version-hash PREVIOUS_VERSION \
  --expected-active-version-hash CURRENT_VERSION \
  --reason 'Restore the previously verified procedure'
```

Admission does not itself prove a learning gain. Later matching remains
model-owned and normal tool policy still applies.

## Keep responsibility finite and visible

Configure a finite readonly assessment using the
[security schedule instructions](../security-scanning.md#finite-recurring-source-audits).
The schedule binds agent, repository, checks, expiry, and run allowance.
Inspect and control that exact task:

```sh
openminion tasks show TASK --agent-id AGENT --json
openminion tasks pause TASK --agent-id AGENT
openminion tasks resume TASK --agent-id AGENT
```

The `security_scan` detail shows admitted and remaining runs, the latest
attempt, and the last successful evidence separately. A failed attempt does
not turn an older successful report into a current clean result. Before the
first attempt, evidence is unavailable rather than successful.

Pause before requesting a separately approved project repair. After verification,
resume the same schedule for a fresh assessment. Restart preserves consumed
allowance; it does not authorize extra runs. Exhaustion, expiry, revocation, or
cancellation stops further admission. A null comparison baseline stays null
unless explicitly changed through the existing schedule contract.

This supports bounded ownership of a declared task. It does not provide
unlimited monitoring, autonomous repair, or security certification.
