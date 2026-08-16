# Ops Tools

The `tools/ops` family provides bounded local, container, and optional SSH
observations. `ops` is the broad system-operations domain; SSH is only one
transport backend.

The tool family exposes eleven tools:

- `ops.target.list`
- `ops.target.inspect`
- `ops.host.snapshot`
- `ops.service.inspect`
- `ops.logs.query`
- `ops.network.inspect`
- `ops.process.inspect`
- `ops.network.port_owner`
- `ops.command.observe`
- `ops.command.plan`
- `ops.command.run`
- `ops.job.inspect`
- `ops.job.cancel`

Observation tools accept closed profile identifiers, not free-form commands or
argument vectors. `ops.command.plan` accepts structured argv and creates an
immutable, expiring plan without execution. `ops.command.run` accepts only the
plan id and hash and remains hidden behind the apply-tier approval profile.
Results carry typed evidence and claim status. Operator
surfaces receive redacted target views: credential references and host-key
material never appear in model-visible or public payloads.

Local and container transports are available by default. Install the `remote`
extra to enable the AsyncSSH transport. SSH targets must configure pinned host
key material or an explicit known-hosts file; ambient SSH config and host-key
trust are not assumed.

Configure targets under `runtime.ops.targets`. This example uses a private key
stored in an environment-backed credential reference; use `password` for a
password credential:

```yaml
runtime:
  ops:
    targets:
      - target_id: staging-web
        kind: ssh
        environment: staging
        address: staging.example.test
        username: openminion
        ssh_auth_mode: private_key
        credential_ref:
          credential_id: staging-web-key
          scope_kind: tool_family
          scope_id: ops
          source_kind: env
          env_name: OPENMINION_OPS_SSH_KEY
          rotation_policy: static
        endpoint_trust:
          host_key: "ssh-ed25519 AAAA..."
        workspace_scopes:
          - /srv/openminion
        timeout_seconds: 30
        max_concurrency: 1
```

The endpoint, username, credential reference, and trust material stay out of
model-facing tool arguments. The model sees only the stable target id and a
redacted target view.

## Operator flow

`opsctl` uses the same configured targets and persistent records as the normal
runtime. Inspect readiness, create a plan, then run the exact reviewed hash:

```bash
opsctl status --config /path/to/openminion.yaml
opsctl command-plan staging-web uname -a --config /path/to/openminion.yaml
opsctl command-run opplan-... PLAN_HASH --confirm \
  --config /path/to/openminion.yaml
opsctl job-inspect opjob-... --config /path/to/openminion.yaml
opsctl evidence-list --target-id staging-web \
  --config /path/to/openminion.yaml
```

Plans, jobs, and redacted evidence are stored below
`OPENMINION_DATA_ROOT/ops/`. A successful exit records process facts only; it
does not claim that a server was semantically configured.

The managed command path is intentionally bounded: one target, structured
argv, no caller-provided shell, no environment/stdin forwarding, no PTY or
file transfer, no bastion/fan-out, and no production or privileged mutation.
Background worker pools and post-dispatch retry remain deferred until a real
long-running command workflow requires them.

Write-safe changes use a separate approval path with an allowed root, stale
state check, atomic replacement, postcondition verification, and rollback.
They are not part of the read-only observation surface.

Built-in ops guidance is injected by tool-family ownership rather than a
separate capability-pack framework. Optional skills can add deeper workflows
such as Linux diagnostics or incident handoff, but the base safety rules stay
with `tools/ops`.

## Debugging evidence owners

Use the narrowest existing owner for each question:

| Question | Owner |
| --- | --- |
| Reproduce, diagnose, patch, and verify an application failure | `/fix <symptom or failing test>` |
| Review ordered durable events for one session | `openminion debug timeline --session <id>` |
| Aggregate executions, model/tool calls, policy, failures, timing, and usage for one invocation | `telemetryctl invocation list`, then `show` or `graph` |
| Inspect current OpenMinion runtime wiring | `openminion debug modules` and `openminion debug module <name>` |
| Inspect normalized runtime readiness and health | `openminion doctor` and current health/status surfaces |
| Inspect a configured host, service, log window, process, or port | the exact `ops.*` observation tool |

Session timeline and telemetry invocation inspection are complementary; one is
ordered session history and the other is an invocation aggregate. OpenMinion
does not copy either into a debugging case or infer root cause from them.

`ops.process.inspect` accepts one typed PID. `ops.network.port_owner` accepts
one typed port and `tcp|udp`. Both use the existing target, fixed-command,
timeout/cancellation, policy, and evidence owners. They do not accept command
text or arbitrary arguments. A missing process/listener or unavailable system
binary is evidence of an incomplete observation, not proof of a root cause.

Read-only diagnosis does not authorize a patch, process signal, service
restart, package install, or remote change. Those actions remain separately
owned and approved.

## Opt-in SSH smoke

The live SSH smoke is separate from deterministic CI. It requires a dedicated
test account and a pinned public host key:

```bash
OPENMINION_LIVE_OPS_SSH=1 \
OPENMINION_OPS_SSH_HOST=ops-test.example \
OPENMINION_OPS_SSH_USER=openminion-smoke \
OPENMINION_OPS_SSH_HOST_KEY='ssh-ed25519 AAAA...' \
OPENMINION_OPS_SSH_PASSWORD='...' \
.venv/bin/python3.11 -m pytest -q \
  tests/e2e/ops/test_live_ssh.py
```

The smoke submits the closed `host.snapshot` profile through the normal service,
durable-job, evidence, and pinned-key transport path. The bounded command path
must first pass deterministic non-production tests before adding a harmless
live command to this smoke. After the run, revoke or rotate the dedicated
credential and remove its temporary target entry. Do not reuse production
credentials for this check.
