---
name: system-inventory
description: Collect a local system inventory and write structured reports.
author: hermes-fixture
platforms: [darwin, linux]
prerequisites: [python]
---

# Purpose

Collect local platform, memory, disk, and runtime facts without changing the
system.

# Procedure

- Read references/collection-guide.md before collecting facts.
- Collect the requested facts with the available system tools.
- Write the report as Markdown and JSON.

# Verification

- Confirm both reports exist and contain the same platform facts.
