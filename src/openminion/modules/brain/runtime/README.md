# `modules/brain/runtime/` — BrainRunner consultative helpers

## Scope

`runtime/` owns the **typed helpers BrainRunner consults** during a step:
context packing, escalation classification, policy verification, goal
hierarchy reads, memory writing, meta-rule drift detection, MRDD ticks,
and other read-mostly computations that return typed payloads the loop
can act on.

If a piece of logic *mutates* the step flow (transition, dispatch,
confirmation resume), it belongs in `runner/` — see `runner/README.md`.
If it computes a typed payload the runner consults, it lives here.

## Named contracts

| Surface | File | Role |
| --- | --- | --- |
| Context packing | `context.py` | Builds the LLM-facing context bundle (segments, recent window, summaries, evidence) per turn. Heavy consumer of `modules/context/`. |
| Escalation classifier | `escalation.py` | Decides whether the current step should escalate to confirmation, denial, or replan. Source of the `pending_confirmation_command` typed signal. |
| Policy verification | `verification/policy.py` | Verifies tool calls + risk-tier decisions against the typed policy contract before dispatch. |
| Goal hierarchy + policy | `goal/hierarchy.py`, `goal/policy.py`, `goal/verification.py`, `goal/long_running.py` | Long-running goal model — reads + writes the typed goal tree, validates state transitions. |
| Memory write | `memory.py` | Owner of memory-write side effects for outcome attribution + success-path memory. |
| Meta-rule decision drift | `mrdd/hook.py`, `mrdd/state.py`, `mrdd/tick.py` | Per-tick meta-rule drift detector + state. Reads `MetaDirective` from `modules/brain/meta/`. |
| Long-running goals | `goal/long_running.py` | Continuation-budget + delegation-depth bookkeeping for goals spanning many turns. |
| Continuation budget | `budget/continuation.py` | Per-turn continuation token budget tracker. |
| Verification helpers | `verification/policy.py`, `verification/probe.py`, `verification/thresholds.py` | Policy checks, closure verification facts, and threshold-calibration contracts. |
| Budget helpers | `budget/continuation.py`, `budget/strategy.py` | Continuation-budget checks plus per-mode strategy budget settings. |
| Self-improvement helpers | `improvement/contracts.py`, `improvement/rubric.py` | Typed online self-improvement contracts and self-eval rubric scoring. |
| Drift detection | `drift.py` | Detects model-output drift (e.g., circular tool patterns). |
| Knowledge consolidation | `consolidation.py` | Cross-turn knowledge folding for memory promotion. |
| Recall consultation / decision | `recall/consultation.py`, `recall/decision.py` | Read-mostly recall-vs-recompute gate (RVRH default + heuristic). |
| Failure pattern aggregation | `failures.py` | Aggregates per-turn failure signatures into rolling pattern state. |
| Performance registry | `performance.py` | Per-tool / per-strategy performance metric registry. |
| Plan reconciliation | `reconciliation.py` | Reconciles `state.plan` against observed outcomes. |
| Reasoning / recovery / action approval | `reasoning/`, `recovery/`, `approval/` | Sub-packages with their own typed surfaces. |
| Learning attribution | `attribution.py` | Outcome-weighted scoring + skill-outcome attribution. |
| Regrounding / research composition / review | `regrounding.py`, `research.py`, `review/` | Specialized helpers for research/review strategies. |
| Recurring task shape | `recurrence.py` | Typed shape for recurring / scheduled tasks. |

## Sub-package shape

```
runtime/
├── approval/   # Approval typed surface
├── budget/                     # Continuation + strategy budget helpers
├── goal/                       # Goal policy, hierarchy, verification, long-running runtime
├── mrdd/                       # Meta-rule decision drift helpers
├── reasoning/                  # Reasoning helpers (multi-step ladder)
├── recall/                     # Recall consultation + decision helpers
├── recovery/                   # Recovery helpers (failure-class playbooks)
├── review/                     # Review-analysis helpers
├── improvement/           # Self-improvement contracts + rubric helpers
├── verification/               # Policy / probe / threshold helpers
└── (~30 single-file modules — see table above)
```

## What lives here vs. `runner/`

**Lives in `runtime/`:**
- Read-mostly typed-payload producers BrainRunner *consults*.
- Cross-cutting computations callable from tick, execution dispatch, or
  post-execution without owning step-loop semantics.
- Side effects scoped to a single concern (memory write, goal write,
  performance metric).

**Lives in `runner/`:**
- Anything BrainRunner calls *to drive a step forward*: tick dispatch,
  transitions, delegate resolution, confirmation handling, lifecycle.

**Heuristic:** ask "would this be called by something OTHER than
BrainRunner.step (e.g., by services/agent or a sibling subsystem)?" If
yes, it probably belongs in `runtime/`.

## Anti-LLM boundary

All payloads in this directory are typed (`extra="forbid"` Pydantic or
typed dataclasses). No prose inspection inside payload builders. Where
a runtime helper consumes LLM output, it converts to typed shape before
returning (see PCHC / GOPP discipline in the broader brain runtime).

## Test depth

- `tests/brain/runtime/` — focused payload + helper tests.
- `tests/brain/test_runner_context.py` — context-packing integration.
- `tests/brain/test_escalation*.py` — escalation classifier.
- `tests/brain/runtime/test_long_running_goals.py` — goal hierarchy.
- `tests/brain/test_mrdd*.py` — meta-rule drift.

## Related charters

- `modules/brain/runner/README.md` — what owns the step loop.
- `modules/context/` — the heavyweight context-packing engine
  `context.py` calls into.
- `services/agent/memory/` — the memory-write side effects
  `memory.py` is wired through.
