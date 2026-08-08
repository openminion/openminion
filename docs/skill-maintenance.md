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

The next useful work is lifecycle and evidence polish:

1. reconcile the canonical skill status reference with the live tracker board,
2. resolve any skill tracker that is simultaneously `in_progress` and 100%
   complete,
3. review older `qa/` skill trackers whose evidence was previously classified
   as placeholder-only,
4. refresh the external delta check when its watch record is old enough to make
   future agents re-derive the same answer.

## Current Board Signals

As of this note, the package-local check found one skill tracker in the
workspace `wip/` bucket that reports 100% completion while still carrying an
`in_progress` overall status:

1. `skill-nl-url-markdown-controlplane-unblock-tracker.md`

That is a lifecycle mismatch first, not a new skill-runtime feature gap.

The same check found older skill rollout trackers in `qa/` that are marked
`done` but were previously triaged as placeholder-evidence surfaces. Treat
those as QA hygiene candidates before opening speculative new skill lanes.

## Recommended Next Order

1. Move or reopen the stuck 100%-complete `wip/` skill tracker based on its
   current evidence.
2. Update the canonical skill status reference so it agrees with the live
   tracker board.
3. Triage the older skill `qa/` rollout trackers and either add real evidence,
   archive them with an explicit historical disposition, or reopen specific
   unresolved rows.
4. Publish a fresh external delta note if no trigger has fired since the last
   watch record.

## Boundaries

Do not use this maintenance pass as permission to add a new skill selector,
classifier, intent heuristic, per-model branch, or skill-specific runtime
shortcut. Skill-side behavior changes still need the normal spec, tracker,
focused tests, and anti-LLM review.

Structural triggers, LLM-owned judgment, and operator-owned catalog commits
remain the governing pattern.
