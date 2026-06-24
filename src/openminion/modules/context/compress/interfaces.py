from typing import Any, Protocol, runtime_checkable

COMPRESS_INTERFACE_VERSION = "v1"


@runtime_checkable
class CompressionServiceAPI(Protocol):
    contract_version: str

    def compress(self, request: Any) -> tuple[Any, str]: ...

    def explain(self, run_id: str) -> Any: ...


@runtime_checkable
class CompactionServiceAPI(Protocol):
    contract_version: str

    def update(self, session_id: str, events: list[Any]) -> Any: ...

    def checkpoint(self, session_id: str, *, reason: str = "manual") -> str: ...

    def get_latest(self, session_id: str) -> Any: ...

    def build_rollover_seed(self, session_id: str, **kwargs: Any) -> Any: ...

    def get_latest_checkpoint(self, session_id: str) -> Any: ...


_REQUIRED_MEMBERS: dict[str, tuple[str, ...]] = {
    "compression_service": ("contract_version", "compress", "explain"),
    "compaction_service": (
        "contract_version",
        "update",
        "checkpoint",
        "get_latest",
        "build_rollover_seed",
        "get_latest_checkpoint",
    ),
}


def ensure_compress_component_compatibility(
    component: Any, *, component_type: str
) -> None:
    normalized = str(component_type or "").strip().lower()
    required = _REQUIRED_MEMBERS.get(normalized)
    if required is None:
        raise ValueError(f"unknown component_type: {component_type}")

    missing: list[str] = []
    for name in required:
        if not hasattr(component, name):
            missing.append(name)
            continue
        value = getattr(component, name)
        if name == "contract_version":
            continue
        if not callable(value):
            missing.append(name)
    if missing:
        raise TypeError(
            f"{component.__class__.__name__} is incompatible with compress {normalized} contract; missing members: {', '.join(missing)}"
        )

    version = str(getattr(component, "contract_version", "")).strip()
    if version != COMPRESS_INTERFACE_VERSION:
        raise TypeError(
            f"{component.__class__.__name__} has unsupported contract_version={version!r}; expected {COMPRESS_INTERFACE_VERSION!r}"
        )
