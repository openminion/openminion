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
approved local database outside OpenMinion before vulnerability scanning. A
missing scanner, rule configuration, or local database is reported as
`unavailable`; it is never converted to a clean scan.

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

The local MVP does not:

- install scanners or download rule/vulnerability databases,
- pull or scan container images,
- use hosted scanner APIs or registry credentials,
- schedule scans,
- scan remote hosts,
- create SARIF or baselines,
- change code, dependencies, policies, or suppressions.

Those capabilities require separately reviewed configuration, policy, and E2E
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
