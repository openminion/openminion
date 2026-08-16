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
/tools activate security_readonly approved=yes dependency=binary:semgrep,binary:trivy
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

Activation does not prove that binaries or local databases exist. Each tool
checks its configured scanner at invocation time and returns its observed
version when available.

## Results and evidence

Results contain scanner identity/version, bounded normalized findings, target,
duration, truncation/partial state, and typed errors. Severity normalization is
a fixed mapping from scanner-native values. Exploitability, business impact,
priority, and remediation remain model-authored interpretations rather than
runtime facts.

`include_evidence_artifact=true` stores a durable redacted JSON evidence artifact
containing the normalized result. It does not store unrestricted scanner
stdout, source bodies, secret matches, credentials, or registry tokens.

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
removing the scanner configuration.
