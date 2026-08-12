from pathlib import Path
from typing import Any

from ..errors import LLMCtlError
from .schemas.catalog import LLMCatalogConfig
from .schemas.policy import AgentLLMPolicy, LLMRoute

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    yaml = None  # type: ignore[assignment]


def load_catalog_config(
    path_or_dict: str | Path | dict[str, Any] | LLMCatalogConfig,
) -> LLMCatalogConfig:
    if isinstance(path_or_dict, (dict, LLMCatalogConfig)):
        return LLMCatalogConfig.model_validate(path_or_dict)

    path = Path(path_or_dict).expanduser().resolve(strict=False)
    if not path.exists():
        raise FileNotFoundError(f"Catalog config not found: {path}")
    if yaml is None:
        raise LLMCtlError(
            "INTERNAL_ERROR",
            "PyYAML is required to load catalog config files",
            {"path": str(path)},
        )
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(parsed, dict):
        raise LLMCtlError(
            "INVALID_ARGUMENT",
            "catalog config must parse to an object",
            {"path": str(path)},
        )
    return LLMCatalogConfig.model_validate(parsed)


def resolve_route(agent_policy: AgentLLMPolicy, purpose: str) -> LLMRoute:
    route = agent_policy.by_purpose.get(purpose)
    if route is not None:
        return route
    if agent_policy.default_route is None:
        raise LLMCtlError(
            "INVALID_ARGUMENT",
            "No route configured for purpose and no default_route",
            {"purpose": purpose},
        )
    return agent_policy.default_route
