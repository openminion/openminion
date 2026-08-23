# Skill Maintenance Status

Status: active maintainer note
Last updated: 2026-08-22

Purpose: give package maintainers a compact skill-side pickup point without
requiring a full reread of the historical tracker set.

This note is not a public feature claim. It records the current maintenance
posture for the skill subsystem as seen from the package checkout.

## Current Reading

The core selection and authoring mechanics remain healthy. The 2026-08-22 work
closed two concrete integration gaps: structural admission of new skill
versions and bounded progressive resources/version pinning for complex skills.

The lifecycle and evidence polish identified on 2026-08-08 has now been
closed in the workspace tracker board:

1. the canonical skill status reference agrees with the live tracker board,
2. no skill tracker remains simultaneously `in_progress` and 100%
   complete,
3. the older `qa/` skill trackers were moved to `done` after fresh validation
   evidence,
4. SSRR now owns admission authority, version re-admission, lifecycle security,
   and Agent Skills conformance;
5. CSPR owns bounded resources and task-plan workflow-version pinning.

## Current Board Signals

The SSRR and CSPR implementation lanes are complete and awaiting independent
validation. ESAE remains a separate postponed evaluation owner and is not part
of either implementation lane.

## Recommended Next Order

1. Run independent QA before marking SSRR and CSPR complete.
2. Keep ESAE postponed unless its operator trigger changes.
3. Open later skill work only when a recorded trigger fires or a concrete user
   request names a behavior gap.
4. Preserve the anti-LLM boundaries below for every future skill lane.

## Boundaries

Do not use this maintenance pass as permission to add a new skill selector,
classifier, intent heuristic, per-model branch, or skill-specific runtime
shortcut. Skill-side behavior changes still need the normal spec, tracker,
focused tests, and anti-LLM review.

Structural triggers, LLM-owned judgment, and operator-owned catalog commits
remain the governing pattern.
