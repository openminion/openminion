# Improvement Notes

## 2026-02-15

1. If user asks for a greeting and no name is provided, default to `world`.
2. If tool scope is missing, return a clear scope-denied message instead of generic failure.
3. Keep greeting output plain text to simplify channel compatibility.
