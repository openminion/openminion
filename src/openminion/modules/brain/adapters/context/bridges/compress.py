from typing import Any, cast

from .shared import BRAIN_ADAPTER_INTERFACE_VERSION, _lazy_resolve_service


class BridgeCompressClient:
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

    def _build_compress_service(self, service_cls: Any) -> Any:
        return service_cls(sessctl=self._store)

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
            return cast(
                str | None,
                compress_svc.get_snapshot(
                    session_id=session_id,
                    agent_id=agent_id,
                    mode_name=mode_name,
                ),
            )
        except Exception:
            return None


def _import_compress_dependencies() -> Any | None:
    try:
        from openminion.modules.context.compress.compaction import CompactionService
    except ImportError:
        return None
    return CompactionService


__all__ = ["BridgeCompressClient"]
