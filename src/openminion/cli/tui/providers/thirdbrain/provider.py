from typing import Any

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION
from openminion.modules.context.knowledge.constants import LAYER_THIRD_BRAIN
from openminion.modules.context.knowledge.models import (
    GraphNeighborhoodRequest,
    GraphPathRequest,
    GraphQueryRequest,
    GraphRefreshRequest,
)


class RuntimeThirdBrainProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(self, knowledge_graphs: Any | None) -> None:
        self._knowledge_graphs = knowledge_graphs

    def list_provider_status(self) -> list[dict[str, Any]]:
        list_sources = getattr(self._knowledge_graphs, "list_sources", None)
        if not callable(list_sources):
            return []
        output: list[dict[str, Any]] = []
        for source in list_sources(layer=LAYER_THIRD_BRAIN):
            health = source.health()
            output.append(
                {
                    "provider": str(source.name),
                    "layer": str(source.layer),
                    "ok": bool(health.ok),
                    "detail": str(health.detail or ""),
                    "tags": list(getattr(source, "tags", ()) or ()),
                    "capabilities": list(source.capabilities.as_tuple()),
                    "diagnostics": dict(getattr(health, "diagnostics", {}) or {}),
                }
            )
        return output

    def search(
        self,
        query: str,
        *,
        provider_names: list[str] | None = None,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        service = self._require_service()
        results = service.query(
            GraphQueryRequest(
                query=str(query or ""), max_results=max(1, int(max_results))
            ),
            provider_names=_provider_names(provider_names),
            layer=LAYER_THIRD_BRAIN,
        )
        return [_query_result_payload(result) for result in results]

    def neighborhood(
        self,
        entity_id: str,
        *,
        provider_names: list[str] | None = None,
        depth: int = 1,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        service = self._require_service()
        results = service.neighborhood(
            GraphNeighborhoodRequest(
                entity_id=str(entity_id or ""),
                depth=max(1, int(depth)),
                max_results=max(1, int(max_results)),
            ),
            provider_names=_provider_names(provider_names),
            layer=LAYER_THIRD_BRAIN,
        )
        return [_query_result_payload(result) for result in results]

    def path(
        self,
        source_entity_id: str,
        target_entity_id: str,
        *,
        provider_names: list[str] | None = None,
        max_hops: int = 4,
    ) -> list[dict[str, Any]]:
        service = self._require_service()
        results = service.path(
            GraphPathRequest(
                source_entity_id=str(source_entity_id or ""),
                target_entity_id=str(target_entity_id or ""),
                max_hops=max(1, int(max_hops)),
            ),
            provider_names=_provider_names(provider_names),
            layer=LAYER_THIRD_BRAIN,
        )
        return [_path_result_payload(result) for result in results]

    def refresh(
        self,
        *,
        provider_names: list[str] | None = None,
        full: bool = False,
    ) -> list[dict[str, Any]]:
        service = self._require_service()
        results = service.refresh(
            GraphRefreshRequest(mode="manual", full=bool(full)),
            provider_names=_provider_names(provider_names),
            layer=LAYER_THIRD_BRAIN,
        )
        return [_refresh_result_payload(result) for result in results]

    def _require_service(self) -> Any:
        if self._knowledge_graphs is None:
            raise RuntimeError("third-brain provider unavailable")
        return self._knowledge_graphs


def _provider_names(provider_names: list[str] | None) -> tuple[str, ...] | None:
    if not provider_names:
        return None
    names = tuple(str(name or "").strip() for name in provider_names)
    filtered = tuple(name for name in names if name)
    return filtered or None


def _query_result_payload(result: Any) -> dict[str, Any]:
    return {
        "provider": str(getattr(result, "provider", "") or ""),
        "layer": str(getattr(result, "layer", "") or ""),
        "tags": list(getattr(result, "tags", ()) or ()),
        "items": [item.to_dict() for item in tuple(getattr(result, "items", ()) or ())],
        "paths": [path.to_dict() for path in tuple(getattr(result, "paths", ()) or ())],
        "omitted": [
            omit.to_dict() for omit in tuple(getattr(result, "omitted", ()) or ())
        ],
        "diagnostics": dict(getattr(result, "diagnostics", {}) or {}),
    }


def _path_result_payload(result: Any) -> dict[str, Any]:
    return {
        "provider": str(getattr(result, "provider", "") or ""),
        "layer": str(getattr(result, "layer", "") or ""),
        "paths": [
            path.to_dict() if callable(getattr(path, "to_dict", None)) else {}
            for path in tuple(getattr(result, "paths", ()) or ())
        ],
        "omitted": [
            omit.to_dict() for omit in tuple(getattr(result, "omitted", ()) or ())
        ],
        "diagnostics": dict(getattr(result, "diagnostics", {}) or {}),
    }


def _refresh_result_payload(result: Any) -> dict[str, Any]:
    return {
        "provider": str(getattr(result, "provider", "") or ""),
        "layer": str(getattr(result, "layer", "") or ""),
        "ok": bool(getattr(result, "ok", False)),
        "refreshed_at": str(getattr(result, "refreshed_at", "") or ""),
        "counts": dict(getattr(result, "counts", {}) or {}),
        "diagnostics": dict(getattr(result, "diagnostics", {}) or {}),
    }


__all__ = ["RuntimeThirdBrainProvider"]
