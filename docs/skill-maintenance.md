# Skill Maintenance Status

Status: active maintainer note
Last updated: 2026-08-08

Purpose: give package maintainers a compact skill-side pickup point without
requiring a full reread of the historical tracker set.

This note is not a public feature claim. It records the current maintenance
posture for the skill subsystem as seen from the package checkout.

## Current Reading

The core skill mechanics are not the next bottleneck. Recent skill work closed
the major runtime, ingest, parser, selection, proposal queue, suggestion, trust,
identity, and final-answer presentation lanes.

The lifecycle and evidence polish identified on 2026-08-08 has now been
closed in the workspace tracker board:

1. the canonical skill status reference agrees with the live tracker board,
2. no skill tracker remains simultaneously `in_progress` and 100%
   complete,
3. the older `qa/` skill trackers were moved to `done` after fresh validation
   evidence,
4. no new skill behavior lane was opened by this maintenance pass.

## Current Board Signals

The package-local check originally found one skill tracker in `wip/` with 100%
completion and seven historical skill trackers in `qa/`. Those have been
closed with current verification evidence in the workspace documentation
lifecycle. Treat future skill work as trigger-based product work, not as
unresolved lifecycle cleanup.

## Recommended Next Order

1. Keep the skill tracker board clean: new skill lanes should enter `wip/`,
   completed lanes should move through `qa/`, and verified lanes should land in
   `done/`.
2. Open new skill work only when a recorded trigger fires or a concrete user
   request names a behavior gap.
3. Preserve the anti-LLM boundaries below for every future skill lane.

## Boundaries

Do not use this maintenance pass as permission to add a new skill selector,
classifier, intent heuristic, per-model branch, or skill-specific runtime
shortcut. Skill-side behavior changes still need the normal spec, tracker,
focused tests, and anti-LLM review.

Structural triggers, LLM-owned judgment, and operator-owned catalog commits
remain the governing pattern.
