# Brain Module

Owner: `openminion-brain`
Shape: `engine-owning`
Runtime peer: paired with `openminion.services.brain`

This module Owns the orchestration engine, adapters, diagnostics, and action-selection/runtime policies for agent reasoning. Primary contracts: `interfaces.py`, `schemas/`, `adapters/*`, `runtime/*`. Typed orchestration payloads live in `schemas/` and diagnostics event surfaces.

Boundary note: cron-resume helpers and path resolution used by brain-owned
runtime flows now live under module ownership at `runner/cron_resume/` and
`paths.py`; service-side imports are compatibility shims only. See
the package-local owner and code-quality guidance.

Loop-closure guidance should stay aligned with the package-local runtime and
contributor guidance.
