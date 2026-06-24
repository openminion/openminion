# `services/supervision/`

Owner: services-layer
Pairs with: standalone (no `modules/supervision/`)

## Purpose

Restart and backoff policy for supervised runtime components.
`SupervisionService` consumes `SupervisionObservation` records
(component-level lifecycle facts) and produces `SupervisionDecision`
records that tell the runtime whether to restart, back off, or fail
fast. Owns the backoff state machine and the supervision policy
operators tune to bound restart storms.

## Public surface

Re-exported from `openminion.services.supervision`:

- `SupervisionService` — runtime entry
- `SupervisionObservation` — lifecycle fact record
- `SupervisionDecision` — restart / backoff / fail-fast decision
- `SupervisionPolicy` — operator-tunable policy
- `RestartDecision` — restart record
- `BackoffState` — backoff state machine record

Internal modules:

- `models.py` — observation / decision / policy / backoff records
- `service.py` — `SupervisionService`

## Owned objects

- `SupervisionService` runtime instance.
- Per-component `BackoffState` records.

## Non-goals

- Process spawn / kill — owned by `services/lifecycle/sidecars.py`
  and `services/runtime/daemon.py`.
- Health probing — owned by `services/health/`.
- Telemetry emission — observations come from `modules/telemetry/`
  consumers.

## Dependencies

- `services/runtime/` — registers components to supervise.
- `services/health/` — provides observations.
- `services/lifecycle/` — receives restart decisions (executes them).
- `base/config/` — operator-tunable policy defaults.

## How this differs from `modules/`

Supervision is a runtime-only concern. No module raises a
"supervision" event in feature terms — modules raise telemetry, and
this package consumes telemetry observations to make a restart
decision. Behavior is symmetric in design to `services/health/`:
health says "is X working", supervision says "should X be restarted".
