# OpenMinion Changelog

Status: active
Last updated: 2026-07-10

This file tracks package-facing release notes for `openminion`.

## Unreleased

- Added typed SophiaGraph namespace filters to existing `memctl` list/search
  commands and local memory-record HTTP routes.
- Preserved all eight namespace dimensions in the integrated SQLite store and
  retained permanent legacy scope compatibility.
- Documented the local-operator security boundary and deterministic namespace
  smoke coverage.

## 0.0.1 - 2026-06-23

### Initial public alpha release

- Added package-local public docs and release-readiness references.
- Hardened first-run CLI behavior for `verify smoke` and default config output.
- Aligned package metadata, root exports, examples, and release-sensitive tests
  to the public package line `0.0.1`.
- Reconfirmed package-local release proof with targeted metadata/version tests,
  root import smoke, `ruff check .`, `make lint`, `python -m compileall
  examples`, and local wheel/sdist builds.

### Notes

- The project is still in alpha.
- This entry establishes the initial public `0.0.1` package line.
