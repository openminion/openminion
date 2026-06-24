# `services/integration/`

Owner: services-layer
Pairs with: standalone (no single `modules/` peer)

## Purpose

Cross-module integration helpers — small runtime bridges that touch
more than one module subsystem and have nowhere else to live. The
package is deliberately a grouped helper bucket per the
`services/README.md` archetypes section.

## Public surface

Currently exported through direct submodule imports (no `__init__.py`
re-exports — consumers import by file). The intended public surface:

- `skill_harness.SkillHarnessResult` — per-skill validation record
- `skill_harness.SkillHarnessReport` — multi-skill report
- `skill_harness.run_skill_harness(root)` — top-level entry
- `skill_harness.discover_skill_roots(root)` — discovery helper
- `skill_harness.validate_skill(skill_root)` — single-skill validator
- `vector_sync.VectorSyncScheduler` — vector-store sync scheduler
  used by the brain bridge during vector adapter wiring

## Owned objects

- `VectorSyncScheduler` instance (one per runtime that uses a vector
  store).
- Per-run `SkillHarnessReport` snapshots.

## Non-goals

- Skill schema / loader — owned by `modules/skill/`.
- Vector-store backend — owned by `modules/storage/`.
- Vector ranking math — owned by `modules/context/`.
- Skill catalog selection — owned by `modules/skill/` and the
  per-turn skill selection in `services/agent/`.

## Dependencies

- `modules/skill/` — skill schema and loader, consumed by the
  harness.
- `modules/storage/` — vector-store interface consumed by the
  sync scheduler.
- `services/runtime/` — scheduler lifecycle hooks.

## How this differs from `modules/`

This is a runtime-glue bucket. Each file is a bridge between two or
more modules that does not belong in any single module owner. New
entries here must be either (a) genuinely cross-module or (b)
runtime-only; otherwise they belong in the relevant module package.
