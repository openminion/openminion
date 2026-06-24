# `modules/brain/runner/` — BrainRunner + tick orchestration

## Scope

`runner/` owns the **per-turn execution loop**: it advances a `WorkingState`
one tick at a time, dispatches tools / decisions / responses through typed
delegates, and writes the resulting `StepOutput` back to the chat surface.

If a piece of logic is "what BrainRunner does step-by-step during a single
user turn," it lives here. If it is a runtime helper that BrainRunner
*consults* (context packing, escalation policy, goal hierarchy, memory
writing), it lives in `runtime/` — see `runtime/README.md`.

## Named contracts

| Symbol | File | Role |
| --- | --- | --- |
| `BrainRunner` | `coordinator.py:47` | Top-level loop owner. Holds `session_api`, `context_api`, `meta_engine`, profile/options, and the typed delegate map. `BrainRunner.step(...)` is the public entry called by `services/agent/`. |
| `RUNNER_DELEGATES` | `delegates.py:490` | Single canonical map from delegate-name → flow/state function. `BrainRunner.__getattr__` resolves missing attrs through this map; auto-generated delegate methods are stamped onto `BrainRunner` at import time in `coordinator.py:497-499`. |
| `_runner_delegate` | `modules/brain/execution/support.py:6` | The cross-module dispatch helper used by tick + execution code to call `_respond_with_meta`, `_respond`, `_decide`, etc. without depending on `BrainRunner` directly. |

## Sub-package shape

```
runner/
├── coordinator.py          # BrainRunner class + delegate-method generator
├── core.py                 # Core step-execution support (used by coordinator)
├── delegates.py            # RUNNER_DELEGATES map — single owner for dispatch
├── transitions.py          # State-transition helpers (phase / status moves)
├── lifecycle.py            # Turn lifecycle hooks (start / persist / emit)
├── call_order.py           # LLM-call ordering invariants
├── turn.py                 # Per-turn interpretation + command semantics
├── resume.py               # Async-job resume path
├── tick/                   # Per-tick orchestration package
│   ├── orchestrator.py     # The tick dispatcher (decide → act → respond)
│   ├── input_processing.py # User-input normalization at tick entry
│   ├── confirmation.py     # Pending-confirmation handling (PCHC-aware)
│   ├── mission_routing.py  # Mission-mode routing for the active tick
│   ├── job_resume.py       # Per-tick resume from async job results
│   └── context.py          # Tick context + confirmation state helpers
└── cron_resume/            # Scheduled / cron-driven resume path
    ├── handler.py          # Cron resume entry
    ├── policies.py         # Resume-eligibility policy
    ├── linker.py           # Cron task ↔ session linker
    ├── cleanup.py          # Stale cron-task reaping
    ├── contracts.py        # Typed cron-resume contracts
    └── text.py             # Cron-resume text rendering
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
  meta-rule decision drift, MRDD hooks.
- Cross-cutting computations that any caller (tick, execution dispatch,
  post-execution) can use without owning step-loop semantics.

**Heuristic:** if it mutates the active step's flow (transition, dispatch,
confirmation resume), it's `runner/`. If it returns a typed payload that
the flow then acts on, it's `runtime/`.

## Anti-LLM boundary

Every typed delegate in `RUNNER_DELEGATES` is structural — no delegate
sniffs prose. PCHC / GOPP / PCHC-2 typed-kind plumbing lives in
`tick/confirmation.py` and uses the shared `RESPOND_KIND_*` constants
from `modules/brain/constants.py`.

## Tests

- `tests/brain/test_runner_*` — focused tests on BrainRunner public surface.
- `tests/brain/runner/` — sub-package focused tests.
- `tests/brain/test_confirmation_replay_bridge_integration.py` — tick-level
  confirmation flow.

## Related charters

- `modules/brain/runtime/README.md` — what runtime helpers exist + their
  scope.
- `modules/brain/loop/adaptive/` — the adaptive tool-loop that BrainRunner
  dispatches into.
- `services/agent/` — the public surface that wraps BrainRunner.
