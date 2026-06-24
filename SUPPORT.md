# Support

## What is supported today

Current public standalone support is limited to:

1. the local-first agent framework surface documented in `README.md`,
   `API_COMPATIBILITY.md`, and `docs/`,
2. package-owned CLI, API, tool, runtime, portability, and example surfaces,
   and
3. the release/install smoke and package-local validation flows used to prove
   the published package.

## Not covered by the standalone public support promise

The following surfaces are outside the current standalone package support
promise:

1. host-specific deployment wiring,
2. third-party provider behavior, outages, pricing, or policy changes,
3. operator-managed infrastructure and secret/config handling around the
   package,
4. repo-planning artifacts, trackers, and maintainer-only workspace process
   docs.

## Getting help

For usage questions or bug reports:

1. include the package version,
2. include the exact import path or command you ran,
3. state whether the issue affects the documented public package surface or a
   deployment/integration path around it,
4. include traceback or reproduction steps when available.

If the issue only reproduces in a larger host deployment or with a specific
provider/integration stack, call that out explicitly; that usually means the
problem is partly or fully outside the standalone package contract.
