# Readonly Security Researcher Example

This example binds the admitted `security-researcher-readonly` skill and
identity to an explicit, non-default agent profile. It audits only trusted,
operator-owned local Git worktrees and publishes unreviewed candidate JSON.

Use your normal OpenMinion configuration for the model provider. Copy the
`identity` block and `security-researcher-readonly` agent entry from
`profile.json`; do not replace your existing default agent.

## Stage and admit the skill

```bash
openminion skill ingest \
  --file examples/skills/security-researcher-readonly/SKILL.md \
  --name security-researcher-readonly \
  --scope agent \
  --agent-id security-researcher-readonly \
  --trust trusted_local
```

Record the returned `version_hash`, then admit that exact staged version. Use
`none` only when the skill has no active version:

```bash
openminion skill admit \
  --skill-id security-researcher-readonly \
  --version-hash <version-hash> \
  --expected-active-version-hash none \
  --target-status verified \
  --reason "Approved local readonly security procedure"
```

## Load the identity

```bash
openminion identity upsert examples/identity/security-researcher-readonly.yaml
```

Start Focus with the configured profile and no project instructions:

```bash
openminion --profile security-researcher-readonly --no-context
```

Inside Focus, enable both independent controls:

```text
/readonly on
/tools activate security_readonly approved=yes
```

Supply the approved target and its full clean Git revision in the audit
request. Do not download rules or databases during the audit.

## Roll back

Deactivate `security_readonly`, remove the agent's `skill` binding, and restore
a previously admitted version with the exact current and prior hashes:

```bash
openminion skill rollback \
  --skill-id security-researcher-readonly \
  --to-version-hash <previous-version-hash> \
  --expected-active-version-hash <current-version-hash> \
  --reason "Restore the previous approved security procedure"
```

The workflow does not remediate findings or scan remote or untrusted targets.
