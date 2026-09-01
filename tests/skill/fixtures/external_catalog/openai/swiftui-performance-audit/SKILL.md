---
name: swiftui-performance-audit
description: Audit a SwiftUI view using the bundled source reference.
---

# Purpose

Audit the bundled SwiftUI view for avoidable render and state-update work.

# Procedure

- Read references/ContentView.swift before producing findings.
- Identify concrete performance risks with source-backed explanations.
- Summarize the smallest useful changes without editing the source.

# Verification

- Confirm every finding cites a symbol present in the bundled source.
