# `services/identity/`

Owner: services-layer
Pairs with: `modules/identity/` (identity records + bundles)

## Purpose

Runtime peer for the identity feature owner. Holds the bootstrap pass
that ensures a default identity profile exists at startup, and the
runtime-side client that resolves identity bundles for agents and
channel actors. Thin layer — most identity logic lives in
`modules/identity/`.

## Public surface

Re-exported from `openminion.services.identity`:

- `ensure_default_profile(...)` — startup pass that guarantees a
  default identity profile exists.

Additional symbols available via direct submodule import:

- `client.IdentityBundleClient` — runtime-side bundle resolver.
- `bootstrap.py` — identity bootstrap pass internals.

## Owned objects

- The runtime `IdentityBundleClient` instance.
- The "default profile exists" invariant at runtime start.

## Non-goals

- Identity record schema — owned by `modules/identity/`.
- Identity verification policy — owned by `modules/identity/`.
- Bundle generation logic — owned by `modules/identity/`.
- Channel authenticity — that is `services/channel/`.
- Identity rotation — currently not implemented; see operational
  backlog (`identity rotate` operator verb is not yet wired).

## Dependencies

- `modules/identity/` — record store, bundle types, verification.
- `services/runtime/bootstrap.py` — invokes `ensure_default_profile`
  during runtime composition.
- `base/config/` — default-profile defaults.

## How this differs from `modules/`

`modules/identity/` owns the identity feature — record shape,
bundles, verification math. `services/identity/` owns only the
runtime wiring: the startup pass that guarantees an identity exists
and the runtime client through which other services resolve bundles
at request time.
