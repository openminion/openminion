# OpenMinion Certification Readiness Matrix

Status: active
Last updated: 2026-06-23

Purpose: summarize the current proof posture for the public `openminion`
package surface.

## Matrix

| Capability area | Public surface | Current proof | Current posture |
| --- | --- | --- | --- |
| install metadata | package metadata, Python version, console scripts | `pyproject.toml`, `README.md`, targeted metadata/version tests, `python -m build --sdist --wheel` producing `openminion-0.0.1` artifacts | alpha-ready |
| root Python API | `import openminion` plus documented root exports | `src/openminion/__init__.py`, API compatibility doc, root import smoke proving `__version__ == 0.0.1` | alpha-ready |
| API runtime composition | `openminion.api.APIRuntime` and root `APIRuntime` export | `src/openminion/api/__init__.py`, API/runtime tests, package-local `ruff check .` and `make lint` | alpha-ready |
| agent wrapper surface | `openminion.Agent`, `AgentRunResult`, `Handoff`, `subagent` | root exports, agent/handoff tests, targeted package regression suite | alpha-ready |
| CLI entrypoint | `openminion` console script and module run path | `pyproject.toml`, README quickstart, CLI smoke gate, package-local lint/validator flows | alpha-ready |
| operator companion CLIs | `openminiond`, `artifactctl`, `memctl`, `brainctl`, `policyctl`, and siblings | `pyproject.toml`, package-local `make lint`, public-surface/layout validators | alpha-ready |
| examples | hello examples, quickstart, `sample` module | `examples/` plus `python -m compileall examples` | alpha-ready |
| package docs | README, docs entrypoint, compatibility/release/source-boundary refs | `README.md`, `docs/`, `API_COMPATIBILITY.md`, `RELEASING.md`, current `0.0.1` package proof | alpha-ready |

## Remaining alpha caveats

These are still true even when the public package surface is documented:

1. deep internal imports are not blanket stable,
2. CLI flags and internal subcommand implementation details can still evolve in
   alpha as long as docs and public boundaries stay honest,
3. the repo contains broader runtime, validator, and integration surfaces than
   the narrow public package contract.

## Current package note

The package-local public release line is currently `0.0.1`.
As of 2026-06-23, local proof covers:

1. targeted package metadata and version tests,
2. root import smoke,
3. package-local `ruff check .`,
4. package-local `make lint`,
5. `python -m compileall examples`,
6. local wheel and sdist build generation.
