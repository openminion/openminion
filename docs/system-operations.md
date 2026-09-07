# Ops Tools

The `tools/ops` family provides bounded local, container, and optional remote
observations. `ops` is the broad system-operations domain; SSH, WinRM,
Kubernetes pod exec, and AWS SSM are transport backends rather than separate
command systems.

The tool family exposes fourteen tools:

- `ops.target.list`
- `ops.target.inspect`
- `ops.host.snapshot`
- `ops.service.inspect`
- `ops.logs.query`
- `ops.network.inspect`
- `ops.process.inspect`
- `ops.network.port_owner`
- `ops.command.observe`
- `ops.file.read`
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

Local and container transports are available by default. Optional transports
use protocol-scoped extras:

- `remote` for AsyncSSH
- `remote-winrm` for WinRM over validated HTTPS
- `remote-kubernetes` for one explicit pod/container exec
- `remote-aws` for one explicit SSM managed node

SSH targets must configure pinned host-key material or an explicit known-hosts
file; ambient SSH config and host-key trust are not assumed. WinRM requires
HTTPS on port 5986 and a CA trust path. Kubernetes targets bind one context,
namespace, pod, and optional container. SSM targets bind one account, region,
managed node, and platform-appropriate allowlisted Run Command document.
Ordinary installations do not install these SDKs.

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
opsctl file-read staging-web /srv/openminion/status.txt --max-bytes 4096 \
  --config /path/to/openminion.yaml
```

Plans, jobs, and redacted evidence are stored below
`OPENMINION_DATA_ROOT/ops/`. A successful exit records process facts only; it
does not claim that a server was semantically configured.

The managed command path is intentionally bounded: one target, structured
argv, no environment/stdin forwarding, no PTY or file transfer, no
bastion/fan-out, and no production or privileged mutation. Kubernetes never
adds a shell wrapper. SSM uses only its target's allowlisted document and never
fans out. `ops.file.read` is limited to configured absolute workspace/log
scopes and a bounded byte count; it does not add write or transfer behavior.
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

## Long-running project execution

`openminion autonomy start` runs a bounded project through durable task
checkpoints. Each cycle loads the latest committed checkpoint, performs one
normal agent turn, runs the configured verifier, and commits only through the
project-cycle claim. Assistant prose never marks the project complete.

Provide a verifier and a cycle limit explicitly:

```bash
openminion autonomy start \
  --goal "Repair the failing package and verify it" \
  --workspace /path/to/project \
  --verification-domain coding \
  --verify-command "python -m pytest -q" \
  --turn-timeout-seconds 900 \
  --verification-timeout-seconds 900 \
  --max-iterations 4
```

The turn timeout applies to each agent cycle; the verification timeout applies
to each configured command. Raise the relevant bound for large coding turns,
builds, or test suites instead of weakening the verifier.

The default `local-safe` permission profile grants file write, copy, and move
inside the selected workspace without an approval prompt. The existing local
path policy still rejects out-of-workspace paths, and other tools keep their
normal policy and approval behavior.

Use `openminion autonomy resume RUN_ID` after a blocked cycle. Persisted agent,
configuration, workspace, verifier, permission profile, and budget selectors
are reused unless the operator supplies an explicit override. An explicit
verification waiver is durable and appears in the proof packet.

`--unattended` is opt-in. It schedules one immediate `projectCycle` job in the
existing cron store; each daemon wake runs at most one fenced cycle and creates
at most one deterministic next wake. Foreground execution remains the default.
Cancellation closes the task and removes its linked wake. Normal chat and
ordinary `agentTurn` cron jobs do not use the project worker.

Project status and report commands expose stable run/task/goal/checkpoint IDs,
the current milestone, committed and remaining cycles, progress/effect/verifier
references, the next wake, blocker, and the latest claim fence:

```bash
openminion autonomy project --task-db /path/to/task.db status TASK_ID
openminion autonomy project --task-db /path/to/task.db report TASK_ID
```

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
