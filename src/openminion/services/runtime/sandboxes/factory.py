"""Sandbox adapter factory."""

from openminion.services.runtime.sandboxes.contracts import SandboxAdapter
from openminion.services.runtime.sandboxes.e2b import E2BSandboxAdapter
from openminion.services.runtime.sandboxes.modal import ModalSandboxAdapter
from openminion.services.runtime.sandboxes.pyodide import PyodideSandboxAdapter

_REGISTRY = {
    "e2b": E2BSandboxAdapter,
    "modal": ModalSandboxAdapter,
    "pyodide": PyodideSandboxAdapter,
}


def build_sandbox_adapter(spec: str) -> SandboxAdapter:
    normalized = (spec or "").strip().lower()
    if normalized not in _REGISTRY:
        raise ValueError(
            f"unknown sandbox spec {spec!r}; expected one of: "
            f"{sorted(_REGISTRY)} (or 'daytona' via build_daytona_runner)."
        )
    return _REGISTRY[normalized]()
