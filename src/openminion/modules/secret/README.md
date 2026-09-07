# `modules/secret/`

Owner: `openminion-secret`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

Stores encrypted local secrets for runtime consumers and resolves explicit
`$SECRET:<key>` references in configuration. The master encryption key comes
from `OPENMINION_SECRET_KEY`; secret values are stored through the configured
record backend.

## Scope

- `SecretService` (the only public symbol)
- Secret errors (`schemas.py`)
- SQLite and record-store persistence (`storage/`)
- Config reference loader (`loader.py`)
- Runtime construction (`factory.py`)

## Non-goals

- Cross-process secret distribution or vault integration
- Credential rotation policy (lives in `modules/runtime/credentials.py`)
- Audit recording of credential access (lives in
  `modules/runtime/credentials.py`)

## Public surface

Re-exported from `openminion.modules.secret`:

- `SecretService`

The module deliberately exposes only the service entry point; everything
else (schemas, internal loaders, storage details) is module-internal.

## Dependencies

- `modules/storage/` — SQLite substrate for cached secret metadata
- `modules/runtime/credentials.py` — typed credential-access events
- `base/` — config, paths, env helpers

## Canonical shape

Canonical with `interfaces.py`, `schemas.py`, `service.py`, `loader.py`,
`factory.py`, `storage/`, and `cli.py`. The narrow public surface is by
design — consumers should depend on `SecretService` and the typed
credential events in `modules/runtime/`.
