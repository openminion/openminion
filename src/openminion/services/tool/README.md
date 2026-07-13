# `services/tool/`

Owner: compatibility imports
Canonical owner: `openminion.modules.tool.selection`

## Purpose

Preserve required import paths for the module-owned selection and exposure
contracts. No tool-selection behavior lives in this package.

## Public surface

- `selection.py` re-exports selection services, records, and provider adapters.
- `exposure.py` re-exports schema-exposure rules for AECR callers.

## Retirement

Remove these facades after the remaining AECR, API, CLI, and test callers use
the canonical module paths.
