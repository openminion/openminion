# System Operations

The system-operations module provides bounded local, container, and optional
SSH observations through the `ops-linux-readonly` capability pack.

The read-only pack exposes nine tools:

- `ops.target.list`
- `ops.target.inspect`
- `ops.host.snapshot`
- `ops.service.inspect`
- `ops.logs.query`
- `ops.network.inspect`
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
They are not part of the read-only pack.

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
  tests/e2e/system_operations/test_live_ssh.py
```

The smoke submits the closed `host.snapshot` profile through the normal
service, durable-job, evidence, and pinned-key transport path. It never accepts
an arbitrary remote command. After the run, revoke or rotate the dedicated
credential and remove its temporary target entry. Do not reuse production
credentials for this check.
