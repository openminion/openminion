# `services/cron/`

Owner: services-layer
Pairs with: standalone (no `modules/cron/`)
Canonical interface version: `CRON_INTERFACE_VERSION`

## Purpose

Owns the cron scheduling fabric for agent runtimes: the scheduler
loop, the store protocol that persists cron records, the delivery
helper that posts cron-triggered turns into the gateway, and the
scheduling-math helpers that compute next-due times and normalize
operator-supplied payloads.

## Public surface

Re-exported from `openminion.services.cron`:

- Scheduler: `CronScheduler`, `CronExecutor`, `CronExecutionResult`,
  `CronEventHook`, `CronDeliveryHandler`, `CronStore`
- Interface contracts: `CRON_INTERFACE_VERSION`,
  `CronSchedulerInterface`, `CronStoreProtocol`, `CronStoreInterface`,
  `ensure_cron_compatibility`, `ensure_cron_store_compatibility`
- Delivery: `HttpPost`, `OutboundSender`, `deliver_cron_result`
- Scheduling: `MisfirePolicy`, `compute_next_due`,
  `default_delete_after_run`, `default_session_target_for_payload`,
  `encode_misfire_policy`, `normalize_delivery`,
  `normalize_misfire_policy`, `normalize_payload`,
  `normalize_schedule`, `normalize_session_target`,
  `normalize_wake_mode`, `parse_iso_datetime`, `to_iso_utc`,
  `utc_now`, `validate_target_payload_pair`

## Current operator safeguards

1. Plain recurring task jobs are expected to honor the task cadence floor of
   `10_000 ms`.
2. Legacy persisted recurring rows below that floor are auto-paused before
   dispatch rather than executed or silently repaired.
3. Pause/resume semantics are owned by the task lifecycle layer and reuse the
   cron enabled flag; the scheduler only respects the persisted enabled state.

## Owned objects

- `CronScheduler` — single live scheduler instance per runtime, owns
  the polling loop / wake timer.
- `CronStore` — protocol-backed record store (SQLite-backed via
  `modules/storage/`).
- `CronEventHook` registrations for cross-runtime notifications.

## Non-goals

- Turn execution itself — that is dispatched into
  `services/runtime/cron/executor.py` via the delivery handler.
- Cron-record SQL schema — owned by `modules/storage/`.
- Operator CLI (`cron list`, `cron add`) — that lives in
  `controlplane/`.

## Dependencies

- `modules/storage/` — record store backend.
- `services/runtime/` — turn executor used by the delivery handler.
- `services/gateway/` — final delivery endpoint when posting results.
- `base/config/` — operator-tunable scheduler intervals.

## How this differs from `modules/`

There is intentionally no `modules/cron/` — cron is purely a runtime
concern (a daemon loop with persistent state). The store protocol and
interface contracts live here, not in `modules/`, because no
non-runtime consumer needs them.
