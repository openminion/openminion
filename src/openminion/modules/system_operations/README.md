# System Operations Module

Owner: `openminion-system-operations`
Shape: `engine-owning`
Runtime peer: standalone (no `services/` peer)

## Purpose

Own typed operation targets, transports, observation profiles, jobs, evidence,
and policy-facing operation requests. The module provides governed system
operation contracts while reusing canonical tool, policy, credential, task,
artifact, and telemetry owners.

## Boundaries

- Skills describe procedures; typed tools perform operations.
- Transports return bounded facts and never make policy decisions.
- Policy and approval owners decide whether an operation may run.
- Credentials are referenced by canonical handles and are never embedded in
  model-visible arguments, events, or evidence.
- CLI and API surfaces adapt module contracts without owning semantics.
- Generic unrestricted execution is not a system-operations capability.

## Public Shape

- `schemas.py`: targets, requests, results, and operation facts
- `interfaces.py` and `transports.py`: transport contracts and implementations
- `registry.py` and `profiles.py`: target and observation-profile lookup
- `policy.py`: operation risk and authorization integration
- `jobs.py`: target-bound durable operation jobs
- `evidence.py`: redacted operation evidence and claim status
- `service.py`: domain composition over these owners
- `api.py` and `cli.py`: transport-neutral views and CLI adaptation
