# OpenMinion Releasing

Status: active
Last updated: 2026-07-03

Purpose: give maintainers a compact package-local release smoke checklist for
the public `openminion` package surface on the active alpha line defined by
`openminion.base.version.OPENMINION_VERSION`.

## Release floor

Before cutting a public package release:

1. keep `README.md` aligned with the actual public package surface,
2. keep `docs/README.md`, `API_COMPATIBILITY.md`, and package-local reference
   docs aligned with the release claim,
3. keep `src/openminion/__init__.py` public exports honest and documented,
4. run repo-required validation gates from `openminion/`.
5. run any tracker-required live confidence checks separately from static repo
   gates; passing lint is not the same thing as a healthy provider-backed CLI
   path.

## Package-local validation

Run from the package root:

```bash
.venv/bin/python3.11 -m pytest -q \
  tests/test_package_metadata.py \
  tests/cli/test_update_check.py \
  tests/test_plugin_manifest.py \
  tests/test_plugin_discovery.py \
  tests/test_plugin_extensions.py \
  tests/runtime/test_plugin_runtime_policy.py \
  tests/extensions/test_registry.py \
  tests/registry/test_registry.py \
  tests/registry/test_registry_postgres_conformance.py \
  tests/a2a/test_google_a2a_v1_conformance.py
.venv/bin/python3.11 -m ruff check .
make lint
```

## Public-surface smoke

Basic import smoke:

Run from the package root:

```bash
.venv/bin/python3.11 - <<'PY'
import openminion
from openminion import APIRuntime, Agent, OpenMinionConfig, tool
from openminion.api import dispatch_request

print(openminion.__version__)
print(APIRuntime.__name__, Agent.__name__, OpenMinionConfig.__name__, callable(tool), callable(dispatch_request))
PY
```

Example smoke:

Run from the package root:

```bash
.venv/bin/python3.11 -m compileall examples
.venv/bin/python3.11 -m build --sdist --wheel --no-isolation
```

## Live confidence checks

When the release tracker requires a runtime-facing confidence pass, use the
package-owned live CLI/E2E runners in addition to the static gates above.

Examples:

```bash
OPENMINION_HOME=/path/to/workspace-root \
OPENMINION_LIVE_CLI_CHAT_E2E=1 \
OPENMINION_LIVE_TOOL_E2E=1 \
.venv/bin/python3.11 tests/e2e/runners/run_cli_chat_e2e_gate.py \
  --config /path/to/workspace-root/test-configs/per-agent-minimax-official.json

OPENMINION_HOME=/path/to/workspace-root \
OPENMINION_LIVE_CLI_CHAT_E2E=1 \
OPENMINION_LIVE_TOOL_E2E=1 \
/bin/bash tests/e2e/runners/run_live_minimax_regression_matrix.sh core
```

These runs validate real provider-backed behavior and can fail even when
`ruff`, `make lint`, import smoke, and local builds are green.

## Docs sync rule

If the public package surface changes, update:

1. `README.md`
2. `docs/README.md`
3. `docs/standalone-claim-alignment.md`
4. `docs/certification-readiness-matrix.md`
5. `docs/runtime-surfaces.md`
6. `API_COMPATIBILITY.md`

Do not rely on workspace-root repo docs alone for package-public claims.

## Publish Sequence

`openminion` follows the same repo-family release path as the sibling package
repos:

1. prepare and validate an RC branch,
2. push an RC tag such as `v<OPENMINION_VERSION>rc1` to publish to TestPyPI,
3. install and smoke-test the RC artifact from TestPyPI,
4. prepare and validate the final non-RC branch,
5. dispatch the `Release` workflow from that final branch with
   `target=testpypi`,
6. install and smoke-test the final TestPyPI artifact,
7. push the final non-RC tag such as `v<OPENMINION_VERSION>` to publish to PyPI,
8. create the GitHub Release using the bare version title, such as
   `<OPENMINION_VERSION>`.

The repo may keep extra hosted validation around build/install/bootstrap smoke,
but the release routing contract should not diverge from the shared family
pattern above.

## GitHub Actions Trusted Publishing

The canonical release workflow for this package is
`.github/workflows/release.yml`.

Trusted publishing must be configured for:

1. TestPyPI environment: `testpypi`
2. PyPI environment: `pypi`
