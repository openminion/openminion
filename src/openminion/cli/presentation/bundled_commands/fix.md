---
description: Run a focused fix workflow
---
Use this focused repair workflow for: $ARGUMENTS

If no target, symptom, failing test, error message, or file path was provided, ask me for the missing target before changing files.

1. Reproduce the problem first using the smallest relevant command, test, log, or manual check.
2. Diagnose the root cause from source code and observed behavior; do not guess from wording alone.
3. Patch the smallest safe owner, preserving existing public contracts and normal tool permissions.
4. Verify the fix with the focused regression first, then any required broader gate.
5. Report final status with the changed files, validation evidence, and any remaining risk.
