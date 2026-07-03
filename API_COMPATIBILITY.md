# OpenMinion API Compatibility

Status: active
Last updated: 2026-07-03

Purpose: record the supported public import roots and entrypoint compatibility
posture for `openminion`.

## Public Python import roots

### Root package

`openminion` is the primary public Python import root.

Current documented root exports:

1. `APIRuntime`
2. `Agent`
3. `AgentOutputValidationError`
4. `AgentRunResult`
5. `Handoff`
6. `MemoryBundle`
7. `OpenMinionConfig`
8. `subagent`
9. `tool`
10. `__version__`

These are defined in `src/openminion/__init__.py`.

### API package

`openminion.api` is also public for explicit runtime/API usage.

Current documented exports:

1. `APIRuntime`
2. `Agent`
3. `AgentOutputValidationError`
4. `AgentRunResult`
5. `Handoff`
6. `dispatch_request`
7. `subagent`

These are defined in `src/openminion/api/__init__.py`.

## Public CLI/operator entrypoints

The following package-owned console scripts are part of the documented operator
surface:

1. `openminion`
2. `openminiond`
3. `openminion-tool`
4. `identityctl`
5. `openminion-controlplane`
6. `runtimectl`
7. `brainctl`
8. `memctl`
9. `sessctl`
10. `contextctl`
11. `ctxctl`
12. `agentregctl`
13. `artifactctl`
14. `openminion-controlplane-telegram`
15. `skill`
16. `skillctl`
17. `a2actl`
18. `rlmctl`
19. `retrievectl`
20. `policyctl`

The script names are defined in `pyproject.toml`.

## Non-promises

These import areas are real package owners, but they are not blanket public
stability promises:

1. `openminion.modules.*`
2. `openminion.services.*`
3. `openminion.tools.*`

Compatibility for those trees is narrower and should be documented surface by
surface instead of assumed from importability.

## Compatibility posture

Current project stage: alpha.

Current public package line: `0.0.2`.

Compatibility expectations for the documented public surface:

1. root exports should change additively by preference,
2. removals or renames should be called out explicitly in release notes,
3. when feasible, package-level compatibility shims should be preferred over
   silent breakage,
4. internal tree moves do not by themselves create a public breaking change
   unless they affect a documented public symbol or command.
