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

You do not need to submit a fix or purchase provider access to report a problem.
We may invite a focused PR and agree on timing. If you cannot contribute code or
collect the requested evidence, say so: maintainers will own the investigation
or fix and record the next step on the issue. See the
[report-to-fix process](CONTRIBUTING.md#from-a-report-to-a-fix).

A merged fix remains open for verification in a published package. The issue's
checklist records what must pass before closure.
