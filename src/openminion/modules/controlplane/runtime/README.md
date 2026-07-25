# Controlplane Runtime

This package owns the synchronous controlplane runtime: routing, dispatch,
persistence handoff, delivery workers, sidecars, metrics, and health probes.

## Dispatch Owner

Inbound dispatch lives in `dispatch/`:

- `dispatch/coordinator.py` keeps `ControlPlaneDispatcher` as the public sync
  coordinator and preserves the legacy constructor/import path.
- `dispatch/wizard.py`, `dispatch/command.py`, `dispatch/chat.py`, and
  `dispatch/clarify.py` own the behavior surfaces behind that coordinator.

See `dispatch/README.md` for the dispatch flow and owner boundaries.
