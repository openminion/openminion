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
- `ops.job.inspect`
- `ops.job.cancel`

Observation tools accept closed profile identifiers, not free-form commands or
argument vectors. Results carry typed evidence and claim status. Operator
surfaces receive redacted target views: credential references and host-key
material never appear in model-visible or public payloads.

Local and container transports are available by default. Install the `remote`
extra to enable the AsyncSSH transport. SSH targets must configure pinned host
key material or an explicit known-hosts file; ambient SSH config and host-key
trust are not assumed.

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
durable-job, evidence, and pinned-key transport path. It never accepts an
arbitrary remote command. After the run, revoke or rotate the dedicated
credential and remove its temporary target entry. Do not reuse production
credentials for this check.
