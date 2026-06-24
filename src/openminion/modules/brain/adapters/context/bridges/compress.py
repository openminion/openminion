"""Modules brain adapters context bridges compress."""

from typing import Any

from .shared import BRAIN_ADAPTER_INTERFACE_VERSION, _lazy_resolve_service


class BridgeCompressClient:
    """Bridge adapter to wrap CompressionService for ContextCtlService."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, backing_store: Any) -> None:
        self._store = backing_store
        self._compress_svc: Any | None = None

    def _resolve_compressctl(self) -> Any | None:
        return _lazy_resolve_service(
            self,
            cache_attr="_compress_svc",
            import_loader=_import_compress_dependencies,
            factory=self._build_compress_service,
        )

    def _build_compress_service(self, imported: tuple[str, Any]) -> Any | None:
        kind, service_cls = imported
        if kind == "compaction":
            return service_cls(sessctl=self._store)
        return service_cls()

    def get_snapshot(
        self,
        *,
        session_id: str,
        agent_id: str,
        mode_name: str | None = None,
    ) -> str | None:
        compress_svc = self._resolve_compressctl()
        if compress_svc is None:
            return None
        try:
            if hasattr(compress_svc, "get_snapshot"):
                return compress_svc.get_snapshot(
                    session_id=session_id,
                    agent_id=agent_id,
                    mode_name=mode_name,
                )
            if hasattr(compress_svc, "get_summary"):
                summary = compress_svc.get_summary(session_id=session_id)
                return str(summary) if summary else None
            if hasattr(compress_svc, "store") and hasattr(compress_svc.store, "get"):
                data = compress_svc.store.get(f"session:{session_id}:summary")
                return str(data) if data else None
            return None
        except Exception:
            return None


def _import_compress_dependencies() -> tuple[str, Any] | None:
    try:
        from openminion.modules.context.compress.compaction import CompactionService
    except Exception:
        try:
            from openminion.modules.context.compress.service import CompressionService
        except Exception:
            return None
        return ("compression", CompressionService)
    return ("compaction", CompactionService)


__all__ = ["BridgeCompressClient"]
