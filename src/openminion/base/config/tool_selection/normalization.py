from __future__ import annotations

from openminion.base.config.base import ConfigError


_TSSR_SPEC_REF = "See the tool-selection migration guide."


def _normalize_tool_selection_mode(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "ranked":
        raise ConfigError(
            "'ranked' mode is retired. Use 'typed' (deterministic when "
            "category signal present, else full catalog) or 'deterministic' "
            "(typed only, no fallback). " + _TSSR_SPEC_REF
        )
    if normalized == "hybrid":
        raise ConfigError(
            "'hybrid' mode has been renamed to 'typed'. Update your config's "
            "tool_selection.mode value. " + _TSSR_SPEC_REF
        )
    return normalized if normalized in {"deterministic", "typed"} else "typed"


def _normalize_schema_exposure(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"stub_first", "full"} else "stub_first"


def _normalize_runtime_binding_selection_strategy(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return (
        normalized
        if normalized in {"ordered", "health_first", "cost_first"}
        else "ordered"
    )


def _is_runtime_binding_id(value: str) -> bool:
    token = str(value or "").strip()
    return (
        bool(token)
        and token.startswith("runtime.")
        and len([part for part in token.split(".") if part]) >= 3
    )
