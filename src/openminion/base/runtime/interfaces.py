"""Runtime component protocol contracts."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

RUNTIME_INTERFACE_VERSION = "v1"


@runtime_checkable
class RuntimeRunnerAPI(Protocol):
    contract_version: str
    name: str

    def run_exec(self, spec: Any, sandbox: Any) -> Any: ...

    def fs_write(self, spec: Any, sandbox: Any) -> Any: ...

    def fs_delete(self, spec: Any, sandbox: Any) -> Any: ...

    def net_fetch(self, spec: Any, sandbox: Any) -> Any: ...


@runtime_checkable
class RuntimePolicyAPI(Protocol):
    contract_version: str

    def evaluate(self, tool_call: Any, ctx: Any) -> Any: ...


@runtime_checkable
class RuntimeEngineAPI(Protocol):
    contract_version: str

    def execute_tool_call(self, tool_call: Any, ctx: Any) -> Any: ...


@runtime_checkable
class RuntimeManagerAPI(Protocol):
    contract_version: str

    def start(self) -> None: ...

    def shutdown(self, grace_s: float = 10) -> None: ...

    def submit_turn(self, req: Any) -> Any: ...

    def cancel_turn(self, trace_id: str) -> bool: ...


_REQUIRED_MEMBERS: dict[str, tuple[str, ...]] = {
    "runner": (
        "contract_version",
        "name",
        "run_exec",
        "fs_write",
        "fs_delete",
        "net_fetch",
    ),
    "policy": (
        "contract_version",
        "evaluate",
    ),
    "engine": (
        "contract_version",
        "execute_tool_call",
    ),
    "manager": (
        "contract_version",
        "start",
        "shutdown",
        "submit_turn",
        "cancel_turn",
    ),
}


def ensure_runtime_component_compatibility(
    component: Any, *, component_type: str
) -> None:
    normalized = str(component_type or "").strip().lower()
    required = _REQUIRED_MEMBERS.get(normalized)
    if required is None:
        raise ValueError(f"unknown component_type: {component_type}")

    missing = [
        name
        for name in required
        if not hasattr(component, name)
        or (
            name not in {"contract_version", "name"}
            and not callable(getattr(component, name))
        )
    ]
    if missing:
        raise TypeError(
            f"{component.__class__.__name__} is incompatible with runtime {normalized} contract; missing members: {', '.join(missing)}"
        )

    version = str(getattr(component, "contract_version", "")).strip()
    if version != RUNTIME_INTERFACE_VERSION:
        raise TypeError(
            f"{component.__class__.__name__} has unsupported contract_version={version!r}; expected {RUNTIME_INTERFACE_VERSION!r}"
        )
