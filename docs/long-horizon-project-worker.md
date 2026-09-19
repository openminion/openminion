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
5. Operator controls through Focus and the autonomy CLI.
6. Permission grants and budget-policy state for longer runs.
7. Capability matrices that expose missing or deferred capabilities.
8. Project reports with metrics, outcome classification, proof refs, safety
   notes, and UX notes.
9. Local and optional live E2E harnesses for project-worker scenarios.

## Current Proof Shape

The current alpha proof uses deterministic compressed pilots, a validation-only
2-hour certification support check, and a live interactive CLI smoke proof:

1. a 30-minute local fixture,
2. a 2-hour coding/research fixture,
3. a 24-hour restart/resume fixture,
4. a 72-hour multi-day fixture,
5. a 2-hour interim certification manifest/report check that proves the
   certification path can validate and write a support report, but is not a
   certification pass,
6. a live provider-backed interactive tools scenario when credentials and quota are
   available.

The compressed pilots prove reporting, restart/resume, operator-control,
permission, failure-recovery, and verification-gate behavior without waiting
for real elapsed time.

The interim certification check is support evidence only. It does not replace
the real elapsed 8-hour research and 24-hour code-bearing certification pilots.

## Claim Boundary

The project-worker path is suitable for alpha testing and contributor
iteration. Do not treat it as a finished "give it any complex task and walk
away for days" product claim yet.

Before that claim is made, OpenMinion needs real elapsed 8-hour and 24-hour
certification pilots, a later multi-day pilot for multi-day claims, and
capability-specific proof for the user-facing surfaces involved in the
objective.

## Running Local Project-Worker Checks

In Focus, a plain coding request can produce a structured project proposal.
Approval shows its goal, success criteria, repository, verification commands,
permission profile, and limits. Only explicit approval queues the project;
ordinary chat replies do not approve it. The proposal retains the current
permission profile and must have a usable verifier before work starts.

Use `/project status [RUN_ID]` or `/project show RUN_ID` to inspect a project.
Use `/project pause RUN_ID`, `/project resume RUN_ID`, and
`/project cancel RUN_ID` to control it within its owning session and agent.
Pause takes effect at the next cycle boundary, not as an immediate process kill.
Resume keeps the run and checkpoint identity and reuses a valid linked wake.
Status without an ID is available only when the session has a single project.

Task API responses include an additive `project_report` for project tasks,
using the same report owner as Focus. Existing task fields remain unchanged.
The provider-free composition test covers approval, verifier failure, linked
repair, independent child review and parent acceptance, and a fresh-process
restart. This does not establish live model quality or elapsed-hour reliability.

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

Live interactive CLI scenarios require provider credentials and quota. Local tests should
remain useful even when live-provider proof is unavailable.
