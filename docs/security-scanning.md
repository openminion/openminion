# Local Security Scanning

OpenMinion exposes four bounded local read-only scan tools behind the
`security_readonly` profile:

- `security.scan_code` uses Semgrep with an operator-configured local rule file
  or rule directory.
- `security.scan_dependencies` uses Trivy filesystem vulnerability scanning.
- `security.scan_artifact` uses Trivy filesystem misconfiguration scanning for
  local infrastructure-as-code and configuration artifacts.
- `security.scan_secrets` uses Trivy filesystem secret scanning and never
  returns matched secret values or source bodies.

The model supplies only an approved local target, finding limit, timeout, and
optional redacted evidence-artifact request. Scanner executables, Semgrep rule
configuration, allowed roots, update posture, and credentials remain
operator-owned configuration.

## Local setup

Install Semgrep and Trivy through your normal trusted system setup. OpenMinion
does not install scanners or refresh their databases.

Set a local Semgrep configuration before activating the profile:

```bash
export OPENMINION_SECURITY_SEMGREP_CONFIG=/absolute/path/to/semgrep-rules.yml
```

Optional family-local settings are:

```bash
export OPENMINION_SECURITY_SEMGREP_EXECUTABLE=semgrep
export OPENMINION_SECURITY_TRIVY_EXECUTABLE=trivy
export OPENMINION_SECURITY_ALLOWED_ROOTS=/approved/root
```

Separate multiple allowed roots with the platform path separator. When the
allowed-roots setting is absent, scans stay inside the current OpenMinion
workspace.

Trivy runs with database and checks-bundle updates disabled. Prepare an
approved local database outside OpenMinion before vulnerability scanning.
Missing executables or rule configuration are reported as `unavailable`.
If Trivy exits unsuccessfully without a report, including when its database is absent, the
result is `failed` with `EXEC_ERROR` and its bounded, redacted diagnostic.
Prepare the required database in the scanner's configured cache and retry the
scan explicitly. A setup failure is never converted to a clean scan.

## Focus workflow

Inspect and activate the exact profile in Focus:

```text
/tools status
/tools activate security_readonly approved=yes
```

Then ask for a bounded local audit, for example:

```text
Scan the approved current workspace for source-code and IaC security findings.
Explain only the evidence returned by the scanners and do not modify files.
```

Deactivate the profile when the audit ends:

```text
/tools deactivate security_readonly
```

Activation is a policy decision, not proof that binaries or local databases
exist. `/tools status` checks current executable readiness, and each invocation
checks its own declared executable again before its handler starts. Legacy
`dependency=...` activation arguments remain accepted but are not readiness
evidence.

## Readonly researcher report

The optional researcher workflow adds revision-bound evidence and one
unreviewed candidate report without changing ordinary scan calls. Follow the
complete setup in
[`examples/security-researcher-readonly/README.md`](../examples/security-researcher-readonly/README.md).

For this workflow, select the explicit researcher profile with `--no-context`,
turn read-only mode on, and activate `security_readonly`. Supply the full Git
revision in `expected_target_revision` and request an evidence artifact for
every scan. The target must be the configured workspace's clean local Git
worktree, and the Semgrep configuration must be one readable local file.

`security.publish_report` accepts exactly one canonical scan reference for
each requested check. It verifies the target, revision, permission mode,
scanner identity, and any scanner finding IDs before writing one durable JSON
report. Findings remain `candidate` or `rejected`, and every report remains
`unreviewed`. A report is `completed` only when every check completed without
truncation, `partial` when at least one check is usable and another is not, and
`blocked` when no check is usable.

## Independent review and approved repair

An optional `security-reviewer-readonly` profile adds `security.review_report`
behind the separate `security_review` exposure profile. A different readonly
agent reviews every candidate in a completed source report, publishing an
immutable decision artifact and an accepted-only remediation brief. Scanners,
mutation and delegation remain unavailable to this reviewer.

Follow the [review and repair procedure](../examples/security-reviewer-readonly/README.md)
for complete evidence loading, one accepted finding per approved project,
failing-before verification, isolated delegated repair, independent child
review, explicit parent acceptance, a fresh-revision rescan and follow-up review.
Project launch remains an operator decision. Partial reports cannot enter the
first review lifecycle, and follow-up does not automatically launch another fix.

## Prepare a repair from a scheduled scan

For an enrolled finite local `code` scan, task detail exposes the latest canonical
report and a review handoff when it contains candidates. Select one independently
accepted finding, pause its exact source task, and prepare a clean, separate Git
worktree at the reviewed revision. The candidate must share the source Git common
directory and stay outside the monitored root. The reproducer and its fixtures
must already be tracked at that reviewed revision; keep them outside the approved
write paths.

```bash
openminion tasks pause TASK_ID --agent-id SCANNER_AGENT
git -C SOURCE_ROOT worktree add --detach PATCH_DIR REVIEWED_SHA
openminion autonomy start \
  --goal "Prepare the selected reviewed local repair" \
  --workspace PATCH_DIR --repository PATCH_DIR \
  --verify-command "python -B verify.py" \
  --max-iterations 6 --max-tool-calls 28 --max-wall-clock-ms 600000 \
  --security-source-task-id TASK_ID \
  --security-review-ref REVIEW_REF --security-finding-id FINDING_ID \
  --security-write-path app.py
```

Replace these values with observed IDs and absolute paths. Use the configured
repair agent, provider configuration and project permissions. The four security
arguments are required together; repeat `--security-write-path` for each approved
tracked file. All other tracked files, including the operator's reproducer and
fixtures, remain protected. The verifier must fail normally before repair and
pass unchanged afterward. A missing command, timeout or already-passing baseline
blocks the repair.

Terminal invocation authorizes that bounded launch. In Focus, use `/project start`
with the same security, repository, verifier and budget arguments and approve its
prepared request; the session workspace boundary must contain `PATCH_DIR`.
Evidence or scope changes after preparation require a new request. Ordinary
projects without security arguments retain their existing behavior.

The parent coordinates a synchronous code-bearing child, a distinct readonly
reviewer and explicit acceptance. Only the child can change the approved files.
Model shell execution, mutating Git and arbitrary or nested delegation are unavailable;
the runtime executes only the approved verifier. These restrictions do not claim
operating-system confinement of trusted verifier code.

Successful completion delivers a candidate and proof. It leaves the monitored
root unchanged and the finding unresolved. Inspect the candidate diff, obtain
separate operator approval, and apply that exact change using ordinary Git.
Resume the same paused source task without resetting its run count or expiry.
A complete fresh source rescan and independent follow-up review establish the
finding outcome. Supply the source task ID as `source_task_id` on that follow-up
review to link it to task detail. Generic independent review remains available
without this optional link.

Use the returned run ID to resume an interrupted project. Its original scope,
verifier and consumed budgets survive; a missing checkpoint is reported rather
than recreated. Retained evidence uses existing artifact owners
`security-project:RUN_ID` and `security-schedule:JOB_ID`. After archiving the
required evidence, release only the intended owner with the existing
`artifactctl refs remove-owner collection OWNER_ID` command against the same
configured artifact store.

## Results and evidence

Results contain scanner identity/version, bounded normalized findings, target,
duration, truncation/partial state, and typed errors. Severity normalization is
a fixed mapping from scanner-native values. Exploitability, business impact,
priority, and remediation remain model-authored interpretations rather than
runtime facts.

`include_evidence_artifact=true` stores a durable redacted JSON evidence artifact
containing the normalized result. It does not store unrestricted scanner
stdout, source bodies, secret matches, credentials, or registry tokens.

Researcher reports reference those scan artifacts rather than copying raw
scanner output. Structural telemetry records status, counts, and artifact
references; report prose and source content do not enter telemetry or debug
bundles.

## Safety and proof boundaries

Readonly scanners run as host processes against trusted local source and
operator-controlled scanner configuration; approved roots are not a sandbox
for hostile repositories. The output cap limits returned results after
collection, not peak scanner or host memory usage.

The ordinary host scanner profile does not add the isolated capabilities below.
OpenMinion still does not:

- install scanners or download rule/vulnerability databases,
- pull images during assessment,
- use hosted scanner APIs or registry credentials,
- scan remote hosts,
- change code, dependencies, policies, or suppressions.

External capabilities require separately reviewed configuration, policy, and live
proof. Roll back the local surface by deactivating `security_readonly` or
removing the scanner configuration. Roll back the researcher example by also
removing its profile binding and restoring the previously admitted skill
version with compare-and-swap:

```bash
openminion skill rollback \
  --skill-id security-researcher-readonly \
  --to-version-hash <previous-version-hash> \
  --expected-active-version-hash <current-version-hash> \
  --reason "Restore the previous approved security procedure"
```

## Authorized local lab

The optional `security_lab` profile adds one active-validation path for an
operator-owned synthetic Docker target. It is separate from the readonly
source workflow. It does not support remote targets, credentials, browser or
web tools, SSH, background processes, PTYs, package installation, remediation,
or production testing.

Configure one local Unix Docker socket, a hardened running target with
`network_mode=none`, a locally present worker image pinned by digest, an exact
executable allowlist, resource limits, and the
`security-researcher-local-lab` identity. Follow
[`examples/security-researcher-local-lab/README.md`](../examples/security-researcher-local-lab/README.md)
and start Focus with `--no-context`.

`/tools status` shows whether the lab is configured, unavailable, or ready.
An identified operator must approve `security_lab` for a finite time. Every
command then requires normal confirmation, runs as one direct argv in a
separate non-root worker joined only to the target network namespace, and must
request canonical evidence. Scope or identity drift invalidates approval; the
runtime never falls back to the ordinary sandbox or host.

`security.publish_report` accepts an active-only v2 request containing the
objective, canonical exec evidence refs, candidate or rejected findings, a
summary, and limitations. Target, activation, execution status, and container
identities are resolved from trusted evidence. Reports remain unreviewed and
sensitive local evidence.

## Opt-in live validation

Installed scanner checks can run without a model provider. The Semgrep check
uses `OPENMINION_TEST_INSTALLED_SECURITY=1`. The Trivy check uses the separate
`OPENMINION_TEST_INSTALLED_TRIVY=1` opt-in and exercises dependency, IaC and
synthetic-secret findings, canonical redacted evidence, and a missing-database
failure. An explicit opt-in fails if its required scanner/assets are missing.

Prepare an approved Trivy vulnerability database outside OpenMinion, then
point the tests at that cache. For example, this operator-run preparation step
downloads the official database into the selected cache:

```bash
trivy image --download-db-only --cache-dir /absolute/path/to/trivy-cache
```

Then run the offline checks; the tests disable dependency-identification API
requests and the adapters keep database/check updates disabled:

```bash
export TRIVY_CACHE_DIR=/absolute/path/to/trivy-cache
export OPENMINION_TEST_INSTALLED_SECURITY=1
export OPENMINION_TEST_INSTALLED_TRIVY=1
.venv/bin/python3.11 -m pytest -q \
  tests/tools/security/test_installed_lifecycle.py \
  --basetemp=/absolute/path/to/fresh/scanner-evidence
```

Retain the Trivy version and cache `db/metadata.json` with the test result;
database age affects vulnerability coverage. This proves the selected local
scanner/assets, not every package ecosystem or vulnerability class.

Three tests exercise the external workflows independently. They stay outside
ordinary hosted CI. Run from a development checkout with a fresh evidence
directory for each invocation.

For source remediation, use an approved MiniMax profile with an available key,
the installed Semgrep executable, and an approved positive per-turn token limit:

```bash
export OPENMINION_CLI_FOCUS_E2E_CONFIG=/absolute/path/to/minimax-profile.json
export OPENMINION_CLI_FOCUS_E2E_AGENT=minimax-m2-7
export OPENMINION_SECURITY_LIVE_MAX_TOKENS=APPROVED_PER_TURN_LIMIT
export OPENMINION_LIVE_CLI_FOCUS_E2E=1
export OPENMINION_LIVE_SECURITY_REMEDIATION=1
export OPENMINION_CLI_FOCUS_E2E_ARTIFACT_ROOT=/absolute/path/to/fresh/evidence
.venv/bin/python3.11 -m pytest -q \
  tests/e2e/cli/focus/test_live_security_remediation.py
```

Replace the limit placeholder with the approved positive integer. This uses the
existing runtime token budget per turn, not an aggregate campaign or dollar
cap. Apply any provider-side spend limit separately. The fixture supplies its
local Semgrep rule and disposable source repository. It records distinct
researcher/reviewer sessions, the operator-approved project, child verification
and review, fresh revision, rescan and follow-up artifacts.

For the local lab, first supply a running local Docker socket and two approved,
already-local images pinned by digest. The target image needs `python3` and its
standard library; the worker needs `curl`, `python3` and its standard library.
The Docker daemon must expose cgroup v2 for the resource-limit probes.
Neither image may declare volumes; both must support numeric UID/GID
`10001:10001`. The fixture creates its own synthetic HTTP target and decoy.

```bash
export OPENMINION_TEST_DOCKER_SECURITY_LAB=1
export OPENMINION_TEST_SECURITY_LAB_DOCKER_SOCKET=unix:///absolute/path/to/docker.sock
export OPENMINION_TEST_SECURITY_LAB_TARGET_IMAGE='target-repository@sha256:APPROVED_DIGEST'
export OPENMINION_TEST_SECURITY_LAB_WORKER_IMAGE='worker-repository@sha256:APPROVED_DIGEST'
.venv/bin/python3.11 -m pytest -q \
  tests/e2e/security_lab/test_local_lab.py \
  --basetemp=/absolute/path/to/fresh/docker-evidence
```

After that passes, retain those Docker inputs and the MiniMax inputs above,
choose a fresh evidence directory, then run the single Focus lab scenario:

```bash
export OPENMINION_LIVE_SECURITY_LOCAL_LAB=1
.venv/bin/python3.11 -m pytest -q \
  tests/e2e/cli/focus/test_live_security_local_lab.py
```

The lab harnesses never start Docker, pull/build images, or reuse an existing
target. They remove only their fixture resources. Missing prerequisites fail
an explicitly opted-in test; opt-out skips and provider-free checks do not
establish live proof.


## Isolated archive and offline snapshot assessment

Use the optional `security-researcher-offline` identity and skill with
`security_offline` and `security_reporting` activated in readonly mode.
[Prepare the worker and inputs](../examples/security/offline/README.md) before
starting. Existing host scan calls retain their original trusted-source meaning.

- `security.scan_snapshot` accepts an approved target ID, plain source tar,
  expected SHA256 and one check: code, dependencies, IaC or secrets.
- `security.scan_image` accepts one Docker-save image archive. It inspects package
  inventory without starting the assessed image or pulling from a registry.
- `security.evaluate_aws_snapshot` evaluates supplied offline facts for public
  administrative ingress and unencrypted EBS volumes. Missing resources,
  collection errors or remaining pages produce partial coverage.
- `security.export_sarif` exports canonical scan or source-report evidence as
  SARIF 2.1.0. Candidate/rejected interpretation and incomplete execution remain
  visible. Compatibility is validated against the schema only.
- `security.compare_scans` requires complete, untruncated scans from the same
  target lineage, scanner, worker image and configuration/rules/database.
  It reports exact IDs as added, present or not observed. Different or missing
  provenance produces `not_comparable`; absence never means resolved.

Raw archives are opaque, creating-session artifacts: no text extraction, preview
or retrieval indexing. A fresh session may rescan identical approved archive
bytes; deduplication preserves the original creator and grants no raw-read access.
Active workers retain them through existing collection
references. Normal completion releases those references; ordinary artifact
retention then applies. Export/evaluation tools require the same actual session
as their input. A report or SARIF export does not authorize repair.

The worker uses a local Unix Docker socket, an immutable local image, no network,
non-root execution, a read-only filesystem and input, dropped capabilities and
bounded temporary storage. Defaults are 100 MiB input (or the lower artifact-store
limit), 10,000 archive entries, 512 MiB scratch, 1 GiB memory, 1 CPU, 64 processes,
1 MiB scanner output and 120 seconds. Input/rule/database preparation happens
outside assessment. The bundled worker rejects vulnerability databases older
than its operator-prepared manifest allows (48 hours in the example).

Assessed ignore files cannot suppress the offline Semgrep check. Language and
rule coverage remains finite; a completed scanner invocation is not proof that
all vulnerabilities or languages were assessed.

## Fixed web/API fixture checks

The existing approved lab can run `python3 -B /opt/security/web_checks.py` from
the prepared worker. Approve `python3` and a command deadline of at least 60
seconds plus execution overhead. The command performs seven fixed checks against
loopback port 8080: anonymous denial, permitted user, invalid identity, forbidden
role, forbidden object, header/cookie attributes, and an unfollowed redirect.
Role/object checks require the permitted-user check to succeed. Synthetic
identity headers are fixture inputs, not production authentication support.

Only declared checks/identities are accepted. Responses are capped at 64 KiB,
each request at five seconds and the complete matrix at 60 seconds. Evidence
contains status, marker and header-attribute facts, without bodies, cookie values
or authorization values. Unknown targets, arbitrary routes, real login, browser
sessions and remote scanning remain unsupported. Publish the existing active
lab v2 report from canonical exec evidence.

## Finite recurring source audits

An operator can bind one configured agent to a clean, absolute Git root, exact
checks/tools, pinned asset configuration, expiry and finite run budget through
`OPENMINION_SECURITY_SCHEDULE_CONFIG`. The existing `task.schedule` and cron
owners persist and execute the admission; no separate scheduler is created.
See the [configuration example](../examples/security/offline/README.md).

Each tick resolves the clean revision at runtime and scans its Git archive in
the offline worker. Scope changes, cancellation, expiry or exhausted budget
prevent dispatch. A claimed run is admitted once and has one attempt; restart
cannot silently rerun it. Mid-run source changes preserve partial evidence and
prevent a complete comparison. Completion requires a successful agent turn and
one complete canonical report. Failed worker evidence is retained even when
no report can be published. Failure never replaces the previous successful
report/baseline references.

Each job's report, scan, export, comparison and baseline dependencies are pinned
under collection owner `security-schedule:<job_id>`. Cancellation stops work but
retains history. Explicit history disposal uses the existing artifact reference
removal for that owner, followed by normal GC; other jobs' references remain.

## Declared local coverage

| Surface | Executed fixture coverage | Boundary |
| --- | --- | --- |
| Code | Python and JavaScript eval rules, including hostile ignore files | Fixed rules, not all language vulnerabilities |
| Dependencies | Python requirements and npm lockfile | Prepared Trivy database; npm negative removes dependency |
| Configuration | Terraform and Dockerfile | Local static fixtures, no cloud deployment |
| Secrets | Synthetic GitHub-token fixture | Redacted facts; no real credential validation |
| Images | Vulnerable nginx archive and minimal empty-inventory image | Different fixture inventories, not application remediation proof |
| Web/API | Seven checks on vulnerable/corrected synthetic server | Loopback fixture protocol only |
| AWS | Two seeded/corrected snapshot controls | Offline facts, no SDK/account discovery |
| Reporting | SARIF schema, exact comparison, finite source ticks | No SARIF consumer interoperability or compliance certification |

Other ecosystems, remote hosts, real authentication and live cloud collection
are outside this proven matrix. Package publication is separate from delivery
to the development branch.
