# Security Policy

## Reporting a vulnerability

Please do not open a public issue with exploit details.

Instead:

1. contact the project maintainers privately through the security reporting
   channel used for OpenMinion, or
2. if that channel is unavailable, open a minimal private coordination thread
   without exploit details and request a secure handoff path.

## Scope

OpenMinion's security posture follows these rules:

1. report vulnerabilities privately first,
2. do not publish proof-of-exploit details before maintainers have had time to
   assess and respond,
3. include affected version, reproduction steps, and impact summary when
   possible.

## Package boundary

Reports should say whether the issue affects:

1. the public standalone package surface documented by `README.md`,
   `API_COMPATIBILITY.md`, and `docs/`,
2. package-owned CLI, API, runtime, tool, or example surfaces in this repo, or
3. deployment-specific, third-party, or operator-owned infrastructure outside
   the package contract.

## Dependency and integration note

If the issue depends on a specific provider, transport, database, plugin,
browser automation backend, or host deployment shape, call that out explicitly.
That helps determine whether the bug is in the package-owned framework surface
or in an integration/deployment path around it.
