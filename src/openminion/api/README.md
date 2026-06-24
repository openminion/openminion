# API

`api/` owns the HTTP and daemon-facing surface for OpenMinion.

Canonical top-level entry points:
- `runtime.py`: `APIRuntime`, the composition root for building API runtime state from config.
- `turns.py`: `run_turn()` and turn-dispatch exceptions.

Subpackages organize the internal layers:
- `server/`: HTTP server implementation, request dispatch, and streaming transport.
- `routes/`: thin HTTP adaptation only.
- `queries/`: read-only API fetch/report owners.
- `operations/`: write-side API orchestration owners.
- `responses/`: response serialization and error envelopes.
- `core/`: shared API execution and validation helpers.

Current owner contract:
- `routes/` parse inputs, match paths, and map domain errors to HTTP.
- `queries/` stay read-only.
- `operations/` own write-side orchestration that would otherwise bloat routes.
- `runtime.py` remains the composition root and public runtime handle.
- `turns.py` remains the stable public turn facade.

Surface posture:
- `runtime.py` and `turns.py` are the small root facades that should read as
  public-stable entry points.
- `core/`, `operations/`, `queries/`, `responses/`, `routes/`, and `server/`
  are meaningful package owners and should get subpackage charters for
  navigation, but that documentation does not make deep imports blanket stable.

Root allowlist:
- `config.py`
- `constants.py`
- `metrics.py`
- `metrics_registry.py`
- `runtime.py`
- `turns.py`

Not in `api/`:
- top-level process entry points such as `openminion/daemon.py` and `openminion/daemon_main.py`
- CLI transport wiring (`cli/`)
- runtime service wiring (`services/`)
