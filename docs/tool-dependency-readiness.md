# Tool Dependency Readiness

Some OpenMinion tools call operator-installed command-line programs. OpenMinion
can report whether those programs are available, but it never installs,
updates, or downloads them.

## Check Current Readiness

Use the structured CLI report for every visible tool:

```bash
openminion tools list --readiness
```

In Focus, use the compact profile and non-profile report:

```text
/tools status
```

The local API exposes the same current facts through
`GET /v1/tools?readiness=true` and `GET /v1/tools/exposure`. Ordinary
`openminion tools list` and `GET /v1/tools` include only static dependency IDs
and do not run version probes.

Readiness has three states:

- `ready`: the configured executable resolved and its bounded version check
  passed.
- `missing`: the executable did not resolve from configuration or `PATH`.
- `unhealthy`: the executable resolved, but its version check failed or timed
  out.

Readiness covers the executable only. Credentials, network access, Semgrep
rules, Trivy databases, and other family-owned assets retain their existing
checks.

## Initial Tool Families

Security tools use these operator-owned settings when present:

```bash
export OPENMINION_SECURITY_SEMGREP_EXECUTABLE=semgrep
export OPENMINION_SECURITY_TRIVY_EXECUTABLE=trivy
```

Semgrep setup: <https://semgrep.dev/docs/getting-started/>

Trivy setup: <https://trivy.dev/latest/getting-started/installation/>

Google Workspace tools use the existing `runtime.tools.gws` configuration:

```json
{
  "runtime": {
    "tools": {
      "gws": {
        "gws_path": "gws"
      }
    }
  }
}
```

Google Workspace CLI setup: <https://github.com/googleworkspace/cli>

After operator setup or a configuration change, run the readiness command
again. Results are current request-local observations; OpenMinion does not
persist a readiness cache.

## Rollout And Rollback

The dependency fields are additive. Tools without declarations behave exactly
as before. To roll back an operator configuration, remove the executable
override and restart the affected command so ordinary `PATH` resolution is
used. Deactivating an exposure profile hides its tools but does not install or
remove software.

Missing declared dependencies fail before the tool handler starts with
`DEPENDENCY_MISSING` and `reason_code=tool_dependency_missing`. Setup commands
shown in status are presentation-only guidance and are never executed by the
readiness layer.
