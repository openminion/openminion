# `modules/secret/`

Owner: `openminion-secret`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

Resolves and validates secrets (API keys, credentials, provider tokens)
for runtime consumers. Owns the typed secret-spec schema, the loader
that materializes secrets from env / file / vault sources, and the
persistence layer for any locally-cached secret metadata.

## Scope

- `SecretService` (the only public symbol)
- Secret schemas (`schemas.py`)
- Secret persistence (`storage/`)
- Source-specific loaders (`runtime/`)

## Non-goals

- Cross-process secret distribution / vault integration beyond reading
- Credential rotation policy (lives in `modules/runtime/credentials.py`)
- Audit recording of credential access (also lives in `runtime/`)

## Public surface

Re-exported from `openminion.modules.secret`:

- `SecretService`

The module deliberately exposes only the service entry point; everything
else (schemas, internal loaders, storage details) is module-internal.

## Dependencies

- `modules/storage/` — SQLite substrate for cached secret metadata
- `modules/runtime/credential_boundaries` — typed credential-access events
- `base/` — config, paths, env helpers

## Canonical shape

Canonical with `interfaces.py`, `schemas.py`, `service.py`, `runtime/`,
`storage/`, `cli.py`. The narrow public surface (one symbol) is by
design — consumers should depend on `SecretService` and the typed
credential events in `modules/runtime/`.
