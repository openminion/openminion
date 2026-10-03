# Local Security Lab Example

This opt-in profile runs one approved direct command against an operator-owned
synthetic Docker target. It does not authorize remote, production,
authenticated, destructive, or persistent testing.

Before use, replace the worker image placeholder with an already-local image
reference pinned by digest. Prepare a disposable target named
`openminion-security-target` with `network_mode=none`, a read-only root,
non-root user, dropped capabilities, no mounts or ports, and bounded CPU,
memory, and PIDs.

Load and admit the matching skill and identity, then start Focus without
project context:

```bash
openminion skill ingest \
  --file examples/skills/security-researcher-local-lab/SKILL.md \
  --name security-researcher-local-lab \
  --scope agent \
  --agent-id security-researcher-local-lab \
  --trust trusted_local
openminion identity upsert examples/identity/security-researcher-local-lab.yaml
openminion --profile security-researcher-local-lab --no-context
```

An identified human must approve a finite activation:

```text
/tools activate security_lab approved=yes ttl=300 approved_by=<operator> reason="approved synthetic target"
```

Each `exec.run` still requires normal confirmation and must use
`host=sandbox`, `security=deny`, foreground mode, no environment or workdir,
and `include_evidence_artifact=true`. Publish only candidate or rejected
findings whose evidence belongs to the same live activation.

If a host crash leaves a worker, inspect its
`openminion.security_lab.session` label and remove that exact container with
`docker rm --force <container-id>` before approving another run. Deactivate
the profile when the bounded test is complete.
