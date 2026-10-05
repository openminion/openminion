# OpenMinion Releasing

Status: active
Last updated: 2026-10-02

Purpose: give maintainers a compact package-local release smoke checklist for
the public `openminion` package surface on the active alpha line defined by
`openminion.base.version.OPENMINION_VERSION`.

## Release floor

Runtime metadata is separate from source or CLI behavior. The
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

Every final source release must also have an explicit binary disposition. The
`Runtime candidate request` observer verifies the successful production-PyPI
producer, then uses a repository-scoped GitHub App token to start the private
`openminion-packaging` candidate workflow with the exact released version,
source commit, official wheel URL, and SHA-256. The candidate workflow
independently checks the immutable source tag and production PyPI metadata
before downloading those bytes. Candidate creation is automatic; stable binary
promotion remains manual and protected.

Use these completion labels exactly:

1. **package published**: production PyPI and GitHub source release succeeded,
2. **source manifest published**: the null-bound production-wheel record is on
   protected main and passes anonymous readback,
3. **Desktop source update certified**: a later immutable same-wheel record has
   reviewed compatibility bounds and commit-pinned Desktop qualification,
4. **binary runtime published**: signed/native-qualified binary assets and their
   record are public, and
5. **full public E2E passed**: the shipped Desktop completed public discovery,
   Prepare update, old-runtime reply, restart activation, same-chat reply and
   owned-daemon shutdown against the public feed.

The first two labels never imply the last three. Runtime metadata verification
cannot report full public E2E because that result requires a separate packaged
application run.

For each final version, close the release record with one of these binary
dispositions; omission is not a valid completed state:

1. **binary runtime published**: the signed, notarized, native-qualified public
   runtime release and binary manifest are complete, or
2. **binary blocked**: record the private candidate run and the exact unmet
   gate, such as macOS signing/notarization, Windows signing, native
   verification, Desktop compatibility, or immutable public-release review.

Do not describe a package/source-only release as a complete runtime release.

Qualify the publication environment and public feeds before the first use;
local files and passing tests are not deployment evidence. Verify without publishing:

```bash
.venv/bin/python3.11 -m scripts.ci.publish_runtime_manifest --help
.venv/bin/python3.11 -m pytest -q \
  tests/scripts/test_release_manifest.py \
  tests/scripts/test_publish_runtime_manifest.py \
  tests/scripts/test_runtime_release_status.py
.venv/bin/python3.11 -m scripts.ci.runtime_release_status \
  --repository . --latest
```

### Runtime metadata checklist

1. Install a GitHub App on `openminion/openminion-packaging` with only Actions
   write access. Store its app ID as the `RUNTIME_PACKAGING_APP_ID` repository
   variable and its private key as the `RUNTIME_PACKAGING_APP_PRIVATE_KEY`
   repository secret in `openminion/openminion`. The app token is minted only
   after the final source producer is verified and is scoped to that one
   private repository.
2. Land the publisher workflow/helpers and initial empty `releases/runtime/v1/`
   feeds through the normal `dev` → `main` PR. Ship the Desktop reader pointing
   to `https://raw.githubusercontent.com/openminion/openminion/main/releases/runtime/v1/pypi/channels/stable.json`.
3. Configure the protected `runtime-publication` environment's reviewers and
   deployment refs, permit Actions PR creation and retain main's existing
   required checks. Do not grant a protection bypass. GitHub-token-created
   PR workflows can require **Approve workflows to run** in GitHub;
   see [GitHub's trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).
4. Before creating the final source tag, confirm the reviewed
   `openminion/openminion-packaging` main workflow and all three target locks are
   current. Do not pre-write a production wheel URL or digest: those immutable
   facts do not exist until PyPI accepts the release. The trusted source
   observer resolves and verifies them from version-specific production PyPI
   metadata, then the packaging workflow independently verifies the same
   URL/digest and immutable source tag before building.
5. Use the existing package release process below. A successful final
   production-PyPI `Release` run triggers **Runtime manifests** automatically;
   it verifies official/producer wheel equality, publishes immutable R then
   proposed feed F to `runtime-publication/<release-id>`, and opens/reuses a PR
   targeting main. `pending_merge` is not published and does not notify clients.
   RC/alpha/beta tag runs and manual TestPyPI runs do not request publication.
6. Confirm `Runtime candidate request` succeeded and that the corresponding
   private `Runtime candidates` run accepted the exact version, source commit,
   production wheel URL, and SHA-256. A failed request, PyPI/tag mismatch, or
   failed native matrix is a visible **binary blocked** disposition; it must not
   be omitted from release closeout.
7. Run the normal PR checks, then merge with a **merge commit**. Keep only one
   pending runtime metadata PR; rerun another producer's observer after the
   first merges. If main moved while a PR was pending, merge current main into
   that transient branch normally and resolve feed conflicts without dropping
   references; no force/rebase, branch recreation or automatic conflict repair.
8. The read-only **Runtime manifests / verify-main** job runs after a manifest
   change is merged into main. Manual workflow dispatch also verifies main and
   never publishes. It checks anonymous main feeds, record digests and R-SHA
   ancestry/readback. Record the actual successful job and metadata/merge SHAs;
   a failed readback remains publication-unconfirmed.
9. Back-merge main into dev through the normal integration process. In a fresh
   checkout, verify the manifest trees match:

   ```bash
   git fetch origin main dev
   git diff --exit-code origin/main origin/dev -- releases/runtime/v1
   ```

10. Verify the Desktop reader against both public feeds before offering an update.
   Record the exact Desktop revision, source tag, producer run/attempt, R/F/merge
   SHAs and artifact digests in the runtime-distribution tracker. Read-only feed
   consumption and a private Electron fixture are separate from shipped-app
   upgrade acceptance.
11. Test the shipped Desktop in isolated roots against public URLs: startup
   and manual checks, explicit **Prepare update**, quit/reopen activation and
   continued replies in the same chat. Source records are initially
   **uncertified** (`desktop_compatibility: null`) and must be skipped, not
   offered. A separately reviewed/tested compatibility revision is required
   before claiming end-to-end upgrade acceptance; this observer does not
   invent or publish that certification. Binary feed stays empty until its
   packaging/native/trust gates pass.
12. Treat observer runs as serialized requests, not a durable queue. If a
   publication run was canceled while another metadata PR was pending, rerun
   the observer for the successful final-tag `Release` producer after the
   pending PR merges. Confirm the `source` job actually ran; a skipped
   observer publishes nothing. If it failed only because another metadata PR
   was open, rerun that observer after the merge. Do not rerun the producer,
   republish PyPI, or regenerate an immutable record to recover sequencing.

### Desktop source certification checklist

Run this only after the source manifest is public. Do not mutate or remove the
original null-bound record.

1. Assign the Desktop a real three-part release version. `0.0.0` is a
   development identity and is rejected as certification evidence.
2. Qualify the exact packaged Desktop and official production wheel on every
   supported target: `darwin-arm64`, `linux-x64`, and `win32-x64`. Evidence must
   include package and `app.asar` digests, exact Desktop/runtime revisions,
   compatibility bounds, verification IDs and all required lifecycle checks.
3. Land the canonical evidence at
   `openminion/desktop@<full-main-commit>:releases/runtime-certification/v1/qualification/<runtime-version>/<evidence-id>.json`.
   The evidence commit must be reachable from Desktop `main`.
4. Manually dispatch **Runtime manifests** with all five source-certification
   inputs: existing runtime version, existing null-bound source release ID, new
   certification release ID, full Desktop evidence commit and evidence ID.
   Partial or mixed binary/certification requests fail before publication.
5. Approve the protected `runtime-publication` deployment. The publisher
   re-verifies the official PyPI wheel, exact existing source record, Desktop
   main ancestry, evidence bytes, target coverage and bounds before opening a
   metadata-only PR.
6. Review and merge that PR with a merge commit. Confirm `verify-main` and the
   exact status report succeed, then back-merge main into dev.
7. Run the shipped Desktop against the public feed. Only that later packaged
   run can establish **full public E2E passed**; certification publication alone
   establishes only **Desktop source update certified**.

Audit one exact version without changing public state:

```bash
.venv/bin/python3.11 -m scripts.ci.runtime_release_status \
  --repository . --version X.Y.Z --require desktop-source
```

The status report always leaves `full_public_e2e` false because repository
metadata cannot substitute for the packaged public acceptance run.

### Binary runtime publication checklist

1. Confirm the automatically requested `Runtime candidates` run in
   `openminion/openminion-packaging` used the released version and full source
   commit plus the exact production PyPI wheel URL and SHA-256. Require all
   native matrix jobs and the aggregate candidate job to pass. The private
   draft release is evidence only. A manual rerun must use the same four exact
   values.
2. Require the candidate workflow's protected `runtime-signing` jobs to sign
   and notarize the macOS pair, sign the Windows pair, refresh hashes after
   signing, and retain the exact Linux pair. Require clean-host native checks
   for all three targets and the aggregate `verification.json`.
3. Approve `openminion-packaging` **Runtime promotion**. It re-verifies the
   candidate bundle and publishes a final immutable `openminion/runtime`
   Release tagged `runtime-v<runtime-version>-<release-id>`. The promotion must
   stop there; it must not request stable metadata before Desktop qualification.
4. Dispatch Desktop **Runtime qualification** for that exact release tag/ID and
   a real Desktop version. Review and merge the generated evidence PR containing
   all three package/ASAR/CLI/daemon hashes, native verification IDs and
   packaged lifecycle results. Record its full Desktop-main commit and evidence
   ID. `0.0.0`, partial targets or workflow artifacts not merged to main fail.
5. Manually dispatch `Runtime manifests` on `openminion/openminion` with the
   release tag/ID and Desktop evidence commit/ID. Approve the
   `runtime-publication` environment. The job independently verifies Release
   immutability and bytes plus Desktop-main ancestry/evidence before opening the
   metadata-only PR.
6. Run required PR checks and merge with a merge commit. Confirm `verify-main`
   succeeds against anonymous public URLs; only then can Desktop discover the
   binary release.
7. Back-merge main into dev and run the unchanged shipped packaged Desktop in
   public target mode on every claimed host. Record the candidate, signing,
   native qualification, Desktop evidence, immutable Release, publication,
   record/feed/merge and post-public acceptance identities in the tracker.

Closeout proof for a promoted binary version:

```bash
.venv/bin/python3.11 -m scripts.ci.runtime_release_status \
  --repository . --version X.Y.Z --require binary
```

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
OPENMINION_CLI_FOCUS_E2E_CONFIG=/path/to/workspace-root/test-configs/per-agent-minimax-official.json \
OPENMINION_CLI_FOCUS_E2E_AGENT=minimax-m2-7 \
OPENMINION_LIVE_CLI_CHAT_E2E=1 \
OPENMINION_LIVE_TOOL_E2E=1 \
.venv/bin/python3.11 tests/e2e/runners/run_cli_e2e_gate.py live

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

## 0.1 milestone gate

The first `0.1` source/CLI release uses `golden-rc` once because it activates
the `0.1.x` compatibility contract and changes the release artifact matrix.
Do not use the routine express path for this milestone.

Before preparing the first `0.1` release candidate:

1. merge or explicitly defer every release-targeted OpenMinion change and
   require green hosted checks on the exact `dev` revision,
2. keep the documented Python exports, CLI entry points, README, changelog and
   `API_COMPATIBILITY.md` aligned with the intended `0.1.x` contract,
3. pass provider-free CLI, Focus, daemon, session-resume, tool and search
   regression gates,
4. pass the bounded MiniMax live gate for the exact candidate and record any
   provider or quota limitation separately from deterministic regressions,
5. publish the release candidate only to TestPyPI and pass an independent clean install,
6. require the hosted Release workflow's wheel and sdist lanes to pass on
   Linux, macOS and Windows, and
7. promote only the reviewed candidate source to the final `0.1` release, then complete
   PyPI, GitHub Release, source-manifest publication and branch synchronization.

The 8-hour and 24-hour autonomy pilots do not block this source/CLI milestone
unless the release claims that duration. Signed Desktop binaries also do not
block it when the release record explicitly closes with **binary blocked**.
Neither deferral permits describing the release as a completed Desktop or full
runtime distribution.

## Publish Sequence

`openminion` defaults to an Express Full Release for routine releases. Express
omits only the prerelease `X.Y.Zrc1` tag and RC TestPyPI install. It retains
final-version TestPyPI from reviewed `main`, production PyPI, GitHub Release,
source metadata, signed/native binary publication, binary metadata, protected
approvals, public readback, packaged Desktop acceptance and branch
synchronization.

Use an RC before the sequence below only for a first public release, changed
release workflow/trusted-publisher/build infrastructure, risky dependency or
package-layout change, compatibility/migration-heavy release, desired public
prerelease feedback, or an explicit maximum-assurance request. Record release
mode `express` or `golden-rc` before publishing. When selected, the golden RC
preflight is:

1. prepare and validate `X.Y.Zrc1` in an isolated local checkout,
2. push only `vX.Y.Zrc1` to publish to TestPyPI, and
3. fresh-install and smoke-test the exact RC artifact.

Routine releases skip that preflight and start here:

1. prepare the final non-RC version on `dev`, validate it, and merge its
   reviewed PR into protected `main`,
2. after the required promotion checks pass and the merge reaches `main`,
   dispatch `Release` from `main` with `target=testpypi`,
3. install and smoke-test the final TestPyPI artifact; confirm that successful
   run's `headSha` is still remote `main` HEAD,
4. push the final non-RC tag such as `v<OPENMINION_VERSION>` at that exact
   reviewed commit to publish to PyPI,
5. create the GitHub Release using the bare version title, such as
   `<OPENMINION_VERSION>`,
6. complete source-manifest publication and its post-publication `main` to
   `dev` back-merge,
7. complete the signed/native binary promotion, packaged Desktop qualification,
   binary-manifest publication and public packaged-runtime acceptance, and
8. perform the final `main` to `dev` back-merge and verify the public source and
   binary feeds.

Do not publish from a dirty local checkout just because the worktree happens to
be sitting on `main`. If `main` moves after the final TestPyPI publish, or the
final artifact needs changes, stop and use a new version; TestPyPI cannot
replace an uploaded final filename.

If a package-code back-merge happens after the GitHub Release but before
runtime metadata approval, it does not complete release synchronization. After
each source or binary metadata PR merges into `main`, back-merge `main` into
`dev` again and verify identical `releases/runtime/v1/` trees.

The `Release` workflow runs only for publication events: manual final-version
TestPyPI dispatches and `v*` tags. Required CI owns promotion and metadata PR
validation, so ordinary PRs and `main` pushes do not repeat the package release
suite. Manual production PyPI dispatch is intentionally unavailable; a final
non-prerelease tag is the only production publication event.

### Independent index acceptance

For the final TestPyPI version, and for the RC when the golden path is selected,
use the version-specific TestPyPI JSON to select the exact wheel URL and
SHA-256. Install that direct URL with dependencies from production PyPI in a
fresh Python 3.11 environment; do not use a mixed index search that might
choose the package from PyPI instead.
Install the final production version from PyPI in a separate fresh environment.
For each, check `pip check`, `python -m openminion --version`, public imports,
and `python -m openminion verify smoke` with isolated home/data/config paths.

For example, from a scratch directory outside the package checkout, set
`VERSION` to the exact final TestPyPI version, or the RC version when used, and
run:

```bash
VERSION=X.Y.Z
curl -fsSL "https://test.pypi.org/pypi/openminion/$VERSION/json" > testpypi.json
test "$(jq '[.urls[] | select(.packagetype == "bdist_wheel")] | length' testpypi.json)" = 1
WHEEL_URL=$(jq -r '.urls[] | select(.packagetype == "bdist_wheel") | .url' testpypi.json)
WHEEL_SHA=$(jq -r '.urls[] | select(.packagetype == "bdist_wheel") | .digests.sha256' testpypi.json)
python3.11 -m venv test-install
test-install/bin/python -m pip install --index-url https://pypi.org/simple/ "$WHEEL_URL#sha256=$WHEEL_SHA"
test-install/bin/python -m pip check
test "$(test-install/bin/python -m openminion --version)" = "$VERSION"
test-install/bin/python -c 'from openminion import APIRuntime, Agent, OpenMinionConfig, tool; from openminion.api import dispatch_request; assert callable(tool) and callable(dispatch_request)'
OPENMINION_HOME="$PWD/home" OPENMINION_DATA_ROOT="$PWD/data" \
  test-install/bin/python -m openminion --config "$PWD/config.json" config init --provider echo --force
OPENMINION_HOME="$PWD/home" OPENMINION_DATA_ROOT="$PWD/data" \
  test-install/bin/python -m openminion --config "$PWD/config.json" verify smoke
```

Use a different fresh directory/venv for the other index and release stage;
replace the direct URL with `openminion==$VERSION` for the production PyPI
install. Run the public import check shown above with the installed venv's
Python, not with a checkout `PYTHONPATH` or its editable environment.

The final TestPyPI dispatch and final-tag PyPI run rebuild separately. Before
tagging, verify the successful TestPyPI run's `headSha` equals remote `main`
HEAD using the shared process's exact command. Even identical source trees can
yield different wheel archive hashes; retain both index hashes and do not
claim exact artifact promotion. The source runtime record must match the
production PyPI wheel's filename, size, and SHA-256, not TestPyPI's.

Keep an evidence row per release with the selected mode, the RC TestPyPI run
when used, final TestPyPI run, final producer run and tag SHA, both index hashes
and install-smoke results, metadata observer run/attempt, metadata PR merge
commit, successful `verify-main` run, and post-publication back-merge PR. Record
a private binary candidate separately from a signed final runtime Release and
public binary-feed verification.

Historical example (2026-09-21; not proof for later versions): RC
TestPyPI run `35599400531`, final TestPyPI run `35599893339`, final-tag
production run `35602030392`, source metadata observer `35602321089`, metadata
PR `#115`, successful main readback run `35659613201`, and `main` to `dev` PR
`#116`. The final TestPyPI and production wheel hashes differed, but their
source trees and installed package files matched; separate clean installs and
smoke checks passed. That final TestPyPI run used an older release branch; the
current standard dispatches from reviewed `main` instead. Packaging candidate
run `35605890344` succeeded only as a private draft, not a signed public binary
or Desktop upgrade.

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
