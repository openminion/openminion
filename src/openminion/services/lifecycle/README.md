# `services/lifecycle/`

Owner: compatibility imports

## Purpose

Preserve existing import paths for callers that have not yet moved to the
canonical runtime ingress, brain improvement, and runtime sidecar owners. This
package contains no lifecycle behavior.

## Public surface

- `request_orchestrator` imports from `services.runtime.ingress.orchestrator`.
- `self_improvement` imports from `modules.brain.runtime.improvement`.
- `sidecars` imports from `services.runtime.sidecars`.

## Retirement

Remove each import-only facade after its remaining API, CLI, service, and test
callers migrate to the canonical path.
