# Capability Packs

Capability packs bundle typed tool metadata, skills, policy, evaluation, audit,
registry, and risk contracts under one domain-owned manifest. They do not add a
second plugin system or grant permissions by themselves.

OpenMinion resolves a pack against the current tool and skill catalogs, checks
policy and runtime availability, and returns an activation report with explicit
disabled reasons. Pack activation is therefore a discoverability and contract
operation, not a security bypass.

The generic framework ships with two fixtures:

- `ops-linux-readonly`, the system-operations pack.
- `business-support-fixture`, a non-operations proof that the framework is not
  coupled to one domain.

Use the capability-pack API or CLI to list, inspect, activate, and smoke-check
packs. Applications should preserve the returned status and disabled reasons
rather than replacing them with inferred prose.
