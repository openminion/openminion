# `services/bootstrap/`

Owner: services-layer
Pairs with: standalone (no `modules/` peer)
Canonical builders: `bootstrap_config_manager`, `migrate_data_root`

## Purpose

Startup-time orchestration that runs before the rest of the service
layer composes. Hosts the config-manager bootstrap pass, the
data-root migration runner, the onboarding state machine consumed by
CLI / TUI / daemon surfaces, and the shared path helpers used by
those bootstraps.

## Public surface

Currently exported through direct submodule imports (no `__init__.py`
re-exports — consumers import by file). The intended public surface:

- `config.bootstrap_config_manager(manager: ConfigManager)`
  — single canonical config bootstrap pass.
- `migration.migrate_data_root(...)` plus its
  `MigrationItem` / `MigrationReport` records.
- `onboarding.OnboardingStatusService` — surface-shared onboarding
  inspection.
- `onboarding.OnboardingRequestedMode`, `OnboardingTrack`,
  `OnboardingState`, `OnboardingAction`, `OnboardingPlanStep`,
  `OnboardingInspectionRequest`, `OnboardingStatus`,
  `OnboardingPlan`, `OnboardingSurfaceRoute`.
- `onboarding.resolve_surface_onboarding_route(...)`,
  `build_inline_setup_args(...)`, `format_fail_fast_message(...)`.
- `paths.py` — bootstrap-time path helpers (separate from
  `base/paths.py`).

## Owned objects

- The shared `OnboardingStatusService` consumed across CLI / TUI /
  daemon entry points.
- `MigrationReport` records for completed data-root migrations.

## Non-goals

- Canonical path layout — that lives in `base/paths.py`.
- Operator-tunable defaults — they live in `base/config/`.
- Runtime assembly — that is `services/runtime/`.
- Surface-specific (CLI / TUI) UX — surfaces consume this package,
  they do not live here.

## Dependencies

- `base/config/` — config manager type.
- `base/paths.py` — canonical path layout.
- `modules/identity/`, `modules/session/`, `modules/storage/` —
  inspected during onboarding plan resolution.

## How this differs from `modules/`

`modules/` owns feature subsystems; this package owns the one-shot
work that happens before those subsystems are ready to serve traffic
(probing config, planning onboarding, migrating data roots into the
`OPENMINION_DATA_ROOT` contract).
