# OpenMinion Certification Readiness Matrix

Status: active
Last updated: 2026-09-01

Purpose: summarize the current proof posture for the public `openminion`
package surface.

## Matrix

| Capability area | Public surface | Current proof | Current posture |
| --- | --- | --- | --- |
| install metadata | package metadata, Python version, console scripts | `pyproject.toml`, `README.md`, targeted metadata/version tests, `python -m build --sdist --wheel` producing artifacts with the canonical `OPENMINION_VERSION` | alpha-ready |
| root Python API | `import openminion` plus documented root exports | `src/openminion/__init__.py`, API compatibility doc, root import smoke proving `__version__ == OPENMINION_VERSION` | alpha-ready |
| API runtime composition | `openminion.api.APIRuntime` and root `APIRuntime` export | `src/openminion/api/__init__.py`, API/runtime tests, package-local `ruff check .` and `make lint` | alpha-ready |
| agent wrapper surface | `openminion.Agent`, `AgentRunResult`, `Handoff`, `subagent` | root exports, agent/handoff tests, targeted package regression suite | alpha-ready |
| CLI entrypoint | `openminion` console script and module run path | `pyproject.toml`, README quickstart, CLI smoke gate, package-local lint/validator flows | alpha-ready |
| operator companion CLIs | `openminiond`, `artifactctl`, `memctl`, `brainctl`, `policyctl`, and siblings | `pyproject.toml`, package-local `make lint`, public-surface/layout validators | alpha-ready |
| examples | hello examples, quickstart, identity bundle, `sample` module | `examples/`, `tests/examples/test_examples.py`, sample CLI and skill-fixture tests, plus compile and lint checks | alpha-ready |
| package docs | README, docs entrypoint, compatibility/release/source-boundary refs | `README.md`, `docs/`, `API_COMPATIBILITY.md`, `RELEASING.md`, current canonical-version package proof | alpha-ready |
| long-horizon project worker | `docs/long-horizon-project-worker.md`, project-worker E2E runner, autonomy reports | deterministic compressed pilots plus a validation-only 2-hour interim certification support report; full 8-hour and 24-hour real elapsed certification pilots remain pending | alpha substrate, not certification-ready |
| deep technical work | Focus, typed plans, exact tools, checkpoints, delegation, and verifier-backed project reports | deterministic contracts pass; the 2026-09-01 MiniMax campaign made bounded progress but claimed coding completion before the required verifier, plan revision, restart, and multi-file proof | bounded supervised not ready for the tested campaign and corpus |
| memory/context usefulness | memory records, session/context surfaces, scorecard references | local utility and audit evidence exists; live provider-backed ContextCtl proof and paired usefulness claims remain routed to their owning trackers | alpha substrate, claim-gated |

## Remaining alpha caveats

These are still true even when the public package surface is documented:

1. deep internal imports are not blanket stable,
2. CLI flags and internal subcommand implementation details can still evolve in
   alpha as long as docs and public boundaries stay honest,
3. the repo contains broader runtime, validator, and integration surfaces than
   the narrow public package contract.
4. durable local records are not the same as certified long-running autonomy or
   provider-backed memory/context usefulness.
5. validation-only interim reports and compressed pilots are support evidence;
   they do not replace real elapsed certification pilots.
6. deterministic deep-work mechanics do not certify a model profile; the
   complete live corpus and its external verifier must pass before a bounded
   supervised readiness claim.

## Current package note

The package-local public release line is the value of
`openminion.base.version.OPENMINION_VERSION`.
As of 2026-08-15, local proof covers:

1. targeted package metadata and version tests,
2. root import smoke,
3. package-local `ruff check .`,
4. package-local `make lint`,
5. fresh-demo quickstart and identity example smokes,
6. plugin discovery, tool execution, sample CLI, and skill ingest checks,
7. `python -m compileall examples`,
8. local wheel and sdist build generation.
