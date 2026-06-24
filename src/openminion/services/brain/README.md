# Brain Service Package

This package contains the runtime service that wires the brain state machine into the service layer. The entry point is `service.py`, with helpers split into focused modules to keep the service readable and stable.

## Layout

- `service.py`: `BrainBridgeService` entry point. Owns runner wiring and delegates turn helpers.
- `context.py`: small context container shared across service helpers.
- `cli.py`: goal-oriented CLI helpers for the brain runtime.
- `client.py`: `OpenMinionLLMClient` for LLM provider normalization and telemetry emission.
- `metadata.py`: runner-option and profile metadata derivation helpers.
- `factory/`: brain service factory owners.
  - `vector.py`: vector adapter initialization and sync scheduler bootstrapping.
  - `retrieve.py`: retrieve adapter initialization.
  - `rlm.py`: RLM adapter initialization.
  - `adapter.py`: thin adapter wiring helpers for session, tools, context, memory, policy, safety, compress, skill.
- `post_execution/`: service postprocessing helpers (prepare/reset/follow-up + related utilities).

## Notes

- Behavior should remain unchanged across refactors. Use the e2e suite for regression checks.
- Keep logging strings stable when extracting helpers.
- Loop-closure invariants should stay aligned with the package-local runtime
  and contributor guidance.
