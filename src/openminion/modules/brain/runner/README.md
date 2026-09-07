# `modules/brain/runner/` — BrainRunner + tick orchestration

## Scope

`runner/` owns the **per-turn execution loop**: it advances a `WorkingState`
one tick at a time, dispatches tools / decisions / responses through typed
delegates, and writes the resulting `StepOutput` back to the chat surface.

If a piece of logic is "what BrainRunner does step-by-step during a single
user turn," it lives here. If it is a runtime helper that BrainRunner
*consults* (context packing, escalation policy, goal hierarchy, memory
writing), it lives in `runtime/` — see `../runtime/README.md`.

## Named contracts

| Symbol | File | Role |
| --- | --- | --- |
| `BrainRunner` | `coordinator.py` | Top-level loop owner. Holds runtime dependencies, profile/options, and the typed delegate map. `BrainRunner.step(...)` is the public entry called by `services/agent/`. |
| `RUNNER_DELEGATES` | `delegates.py` | Canonical map from delegate name to flow/state function. `BrainRunner` exposes those delegates without duplicating wrapper methods. |
| `_runner_delegate` | `../execution/delegation.py` | Dispatch helper used by bootstrap and runner code without depending directly on `BrainRunner`. |

## Sub-package shape

```
runner/
├── coordinator.py          # BrainRunner class + delegate-method generator
├── delegates.py            # RUNNER_DELEGATES map — single owner for dispatch
├── transitions.py          # State-transition helpers (phase / status moves)
├── lifecycle.py            # Turn lifecycle hooks (start / persist / emit)
├── call_order.py           # LLM-call ordering invariants
├── turn.py                 # Per-turn interpretation + command semantics
├── resume.py               # Async-job resume path
├── tick/                   # Per-tick orchestration package
│   ├── orchestrator.py     # The tick dispatcher (decide → act → respond)
│   ├── input_processing.py # User-input normalization at tick entry
│   ├── confirmation.py     # Pending-confirmation handling
│   ├── mission_routing.py  # Mission-mode routing for the active tick
│   ├── job_resume.py       # Per-tick resume from async job results
│   └── context.py          # Tick context + confirmation state helpers
└── cron_resume/            # Scheduled / cron-driven resume path
    ├── handler.py          # Cron resume entry
    ├── policies.py         # Resume-eligibility policy
    ├── linker.py           # Cron task ↔ session linker
    └── contracts.py        # Typed cron-resume contracts
```

## What lives here vs. `runtime/`

**Lives in `runner/`:**
- Anything BrainRunner *calls during step()*: tick dispatch, transitions,
  delegate resolution, confirmation handling, lifecycle hooks.
- Per-tick orchestration code that reads `WorkingState` and writes
  `StepOutput`.

**Lives in `runtime/`:**
- Helpers BrainRunner *consults* but doesn't own: context packing, policy
  verification, escalation classifier, goal hierarchy, memory writer,
  meta-rule decision drift, and other consultative helpers.
- Cross-cutting computations that any caller (tick, execution dispatch,
  post-execution) can use without owning step-loop semantics.

**Heuristic:** if it mutates the active step's flow (transition, dispatch,
confirmation resume), it's `runner/`. If it returns a typed payload that
the flow then acts on, it's `runtime/`.

## Anti-LLM boundary

Every typed delegate in `RUNNER_DELEGATES` is structural; no delegate inspects
prose to choose runtime behavior. Confirmation plumbing lives in
`tick/confirmation.py` and uses the shared `RESPOND_KIND_*` constants from
`modules/brain/constants.py`.

## Tests

- `tests/brain/test_runner_*` — focused tests on BrainRunner public surface.
- `tests/brain/runner/` — sub-package focused tests.
- `tests/brain/test_confirmation_replay_bridge_integration.py` — tick-level
  confirmation flow.

## Related charters

- `../runtime/README.md` — what runtime helpers exist and their
  scope.
- `modules/brain/loop/adaptive/` — the adaptive tool-loop that BrainRunner
  dispatches into.
- `services/agent/` — the public surface that wraps BrainRunner.
