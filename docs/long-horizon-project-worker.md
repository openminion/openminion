# Long-Horizon Project Worker

Status: alpha

OpenMinion includes an early project-worker layer for longer objectives that
need checkpoints, operator controls, permission and budget state, validation
evidence, and final reports.

This is not a finished unattended-autonomy claim. It is a measurable substrate
for long-horizon work that is still being expanded through tests, pilots, and
capability-specific proof.

## What It Provides

1. Project-run projection over existing autonomy and task lifecycle records.
2. Objective, evidence, resume, operator-decision, capability, and metric refs.
3. Checkpoint, restart/resume, and duplicate-active-worker protection.
4. Structured cycle records with evidence and validation refs.
5. Operator controls through the autonomy CLI.
6. Permission grants and budget-policy state for longer runs.
7. Capability matrices that expose missing or deferred capabilities.
8. Project reports with metrics, outcome classification, proof refs, safety
   notes, and UX notes.
9. Local and optional live E2E harnesses for project-worker scenarios.

## Current Proof Shape

The current alpha proof uses deterministic compressed pilots and a live Focus
smoke proof:

1. a 30-minute local fixture,
2. a 2-hour coding/research fixture,
3. a 24-hour restart/resume fixture,
4. a 72-hour multi-day fixture,
5. a live provider-backed Focus tools scenario when credentials and quota are
   available.

The compressed pilots prove reporting, restart/resume, operator-control,
permission, failure-recovery, and verification-gate behavior without waiting
for real elapsed time.

## Claim Boundary

The project-worker path is suitable for alpha testing and contributor
iteration. Do not treat it as a finished "give it any complex task and walk
away for days" product claim yet.

Before that claim is made, OpenMinion needs real elapsed multi-day pilot
evidence plus capability-specific proof for the user-facing surfaces involved
in the objective.

## Running Local Project-Worker Checks

From the package root:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 tests/e2e/runners/run_project_worker_e2e.py list
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 tests/e2e/runners/run_project_worker_e2e.py local
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 tests/e2e/runners/run_project_worker_e2e.py pilot
```

To regenerate compressed pilot artifacts:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 tests/e2e/runners/run_project_worker_e2e.py pilot-artifacts
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 tests/e2e/runners/run_project_worker_e2e.py soak-artifacts
```

Live Focus scenarios require provider credentials and quota. Local tests should
remain useful even when live-provider proof is unavailable.
