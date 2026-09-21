# OpenMinion Releasing

Status: active
Last updated: 2026-09-20

Purpose: give maintainers a compact package-local release smoke checklist for
the public `openminion` package surface on the active alpha line defined by
`openminion.base.version.OPENMINION_VERSION`.

## Release floor

Runtime metadata is separate from source or CLI behavior. The new
`Runtime manifests` workflow observes a successful final-tag `Release` run and
requires its production PyPI job to have succeeded. It reads trusted `main`
helpers under `scripts/ci/`, verifies the official wheel bytes/package metadata,
compares its filename/size/digest with the approved producer's `dist` wheel,
and prepares a metadata PR only after the protected `runtime-publication` environment exists.
Record IDs include the producer run and attempt; publisher-only reruns reuse
the same immutable identity. Producer artifacts are read as data, never executed.
No Desktop-specific command or CLI/TUI startup check is added.

Publication targets protected `main` in this repo, through a transient metadata-only PR:
`releases/runtime/v1/pypi/releases/<version>/<release-id>.json` and
`releases/runtime/v1/pypi/channels/stable.json`. Immutable record readback must
pass before composing the proposed feed; references pin full metadata commit SHAs.
Merge the PR with a merge commit, never squash/rebase, so record SHAs remain
reachable. Desktop reads the stable feed from `main`. Back-merge `main` into
`dev` after publication; both branches retain the same published metadata,
while `dev` can contain unreleased code. Uncertified Desktop bounds remain null.
Existing release records are not rewritten. Binary promotion uses the same
protected metadata-PR boundary but has an independent manual request and trust
gate.

Qualify the publication environment and public feeds before the first use;
local files and passing tests are not deployment evidence. Verify without publishing:

```bash
.venv/bin/python3.11 -m scripts.ci.publish_runtime_manifest --help
.venv/bin/python3.11 -m pytest -q tests/scripts/test_release_manifest.py tests/scripts/test_publish_runtime_manifest.py
```

### Next-release runtime metadata checklist

1. Land the publisher workflow/helpers and initial empty `releases/runtime/v1/`
   feeds through the normal `dev` → `main` PR. Ship the Desktop reader pointing
   to `https://raw.githubusercontent.com/openminion/openminion/main/releases/runtime/v1/pypi/channels/stable.json`.
2. Configure the protected `runtime-publication` environment's reviewers and
   deployment refs, permit Actions PR creation and retain main's existing
   required checks. Do not grant a protection bypass. GitHub-token-created
   PR workflows can require **Approve workflows to run** in GitHub;
   see [GitHub's trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).
3. Use the existing package release process below. A successful final
   production-PyPI `Release` run triggers **Runtime manifests** automatically;
   it verifies official/producer wheel equality, publishes immutable R then
   proposed feed F to `runtime-publication/<release-id>`, and opens/reuses a PR
   targeting main. `pending_merge` is not published and does not notify clients.
   RC/alpha/beta tag runs and manual TestPyPI runs do not request publication.
4. Run the normal PR checks, then merge with a **merge commit**. Keep only one
   pending runtime metadata PR; rerun another producer's observer after the
   first merges. If main moved while a PR was pending, merge current main into
   that transient branch normally and resolve feed conflicts without dropping
   references; no force/rebase, branch recreation or automatic conflict repair.
5. The read-only **Runtime manifests / verify-main** job runs after a manifest
   change is merged into main. Manual workflow dispatch also verifies main and
   never publishes. It checks anonymous main feeds, record digests and R-SHA
   ancestry/readback. Record the actual successful job and metadata/merge SHAs;
   a failed readback remains publication-unconfirmed.
6. Back-merge main into dev through the normal integration process. In a fresh
   checkout, verify the manifest trees match:

   ```bash
   git fetch origin main dev
   git diff --exit-code origin/main origin/dev -- releases/runtime/v1
   ```

7. Verify the Desktop reader against both public feeds before offering an update.
   Record the exact Desktop revision, source tag, producer run/attempt, R/F/merge
   SHAs and artifact digests in the runtime-distribution tracker. Read-only feed
   consumption and a private Electron fixture are separate from shipped-app
   upgrade acceptance.
8. Test the shipped Desktop in isolated roots against public URLs: startup
   and manual checks, explicit **Prepare update**, quit/reopen activation and
   continued replies in the same chat. Source records are initially
   **uncertified** (`desktop_compatibility: null`) and must be skipped, not
   offered. A separately reviewed/tested compatibility revision is required
   before claiming end-to-end upgrade acceptance; this observer does not
   invent or publish that certification. Binary feed stays empty until its
   packaging/native/trust gates pass.

### Binary runtime publication checklist

1. Run `Runtime candidates` in `openminion/openminion-packaging` at a reviewed
   commit. Require all native matrix jobs and the aggregate candidate job to
   pass. The private draft release is evidence only.
2. Sign and notarize the macOS pair, sign the Windows pair, and retain the exact
   Linux pair. Run clean-host native checks and Desktop prepare/restart/reply
   continuity for every advertised target and compatibility range.
3. Create a final immutable `openminion/runtime` GitHub Release tagged
   `runtime-v<runtime-version>-<release-id>`. Upload every exact executable,
   `candidate.json`, and `verification.json`. The verification document must
   map every target to its final native `verification_id`.
4. Manually dispatch `Runtime manifests` on `openminion/openminion` with that
   exact tag and release ID. Approve the `runtime-publication` environment.
   The job independently verifies release immutability, metadata identity,
   GitHub asset digests and sizes, and downloaded bytes before opening the
   metadata-only PR.
5. Run required PR checks and merge with a merge commit. Confirm `verify-main`
   succeeds against anonymous public URLs; only then can Desktop discover the
   binary release.
6. Back-merge main into dev and record the candidate run, signing/notarization
   evidence, native verification IDs, runtime release URL, publication run,
   record/feed commits, and merge commit in the runtime-distribution tracker.

Do not create the final runtime Release or dispatch binary publication while
any signing, native verification, compatibility, or immutable-release gate is
missing. A private candidate and a source PyPI release do not satisfy these
binary gates.

Manifest-only merges run verification and normal CI, not another package
release. Main is the public discovery authority; dev's copy is for consistent
development, not a second update channel. Desktop checks notify only: no
timer-driven installation or restart of active work.

Before cutting a public package release:

1. keep `README.md` aligned with the actual public package surface,
2. keep `docs/README.md`, `API_COMPATIBILITY.md`, and package-local reference
   docs aligned with the release claim,
3. keep `src/openminion/__init__.py` public exports honest and documented,
4. run repo-required validation gates from `openminion/`.
5. run any required live confidence checks separately from static repo gates;
   passing lint is not the same thing as a healthy provider-backed CLI path.

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
make release-check
```

`make release-check` builds the wheel and source distribution and validates
their rendered package metadata. The hosted `Release` workflow separately
installs both artifact types in fresh environments and exercises bootstrap,
quiet demo turns across a process restart, readiness labeling, and missing
provider-credential diagnosis before publication can run.

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
make release-check
```

## Live confidence checks

When a release needs a runtime-facing confidence pass, use the package-owned
live CLI/E2E runners in addition to the static gates above.

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

`openminion` uses this release path:

1. prepare and validate an RC branch,
2. push an RC tag such as `v<OPENMINION_VERSION>rc1` to publish to TestPyPI,
3. install and smoke-test the RC artifact from TestPyPI,
4. prepare and validate the final non-RC branch,
5. dispatch the `Release` workflow from that final branch with
   `target=testpypi`,
6. install and smoke-test the final TestPyPI artifact,
7. push the final non-RC tag such as `v<OPENMINION_VERSION>` to publish to PyPI,
8. create the GitHub Release using the bare version title, such as
   `<OPENMINION_VERSION>`,
9. merge the released `main` commit back into remote `dev`, then update the
   shared local `dev` checkout and verify it is not behind the remote branch.

For `openminion`, step 7 should tag the already-reviewed remote `main` commit.
Do not publish from a dirty local checkout just because the worktree happens to
be sitting on `main`.

OpenMinion release PRs also run hosted lint against the stable
`openminion-eval` `main` branch by default. Normal feature PRs continue to use
the sibling `dev` branch so integration drift is visible before release, but a
`release/*` PR should not be blocked by unrelated unreleased sibling work.
Override this only with `OPENMINION_CI_DEP_BRANCH` when intentionally cutting a
coordinated multi-repo release.

The repo may keep extra hosted validation around build/install/bootstrap smoke,
but the release routing contract should not diverge from the shared family
pattern above.

## Post-release `dev` synchronization

After the release merge-back lands, update the shared `dev` checkout without
discarding local work:

1. inspect the working tree and commit coherent finished changes first,
2. if an integration operation requires a clean tree, use an include-untracked
   stash only as a temporary recovery copy,
3. update local `dev` from remote `dev`, reapply any temporary stash, and resolve
   overlaps against the newer owner implementations,
4. keep the recovery stash until the reapplied tree and validation pass,
5. confirm released `main` is an ancestor of `dev` and local `dev` has zero
   commits behind remote `dev`.

Finally, prove that developer launches use this checkout rather than another
editable install:

```bash
make run-local ARGS='version'
PYTHONPATH="$PWD/src" .venv/bin/python3.11 - <<'PY'
from pathlib import Path
import openminion

print(Path(openminion.__file__).resolve())
print(openminion.__version__)
PY
```

The import path must resolve under the current checkout's `src/openminion`, and
the reported version must match `src/openminion/base/version.py`. Do not use a
package-upgrade prompt as evidence that source-checkout execution is current.

## GitHub Actions Trusted Publishing

The canonical release workflow for this package is
`.github/workflows/release.yml`.

Trusted publishing must be configured for:

1. TestPyPI environment: `testpypi`
2. PyPI environment: `pypi`
3. workflow file name: exactly `release.yml`
4. repo: `openminion/openminion`

If TestPyPI or PyPI trusted publishing points at a different workflow file such
as `publish.yml`, the hosted publish will fail even if the job contents are
otherwise correct.
