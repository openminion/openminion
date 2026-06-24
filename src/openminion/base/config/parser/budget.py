"""Identity budget parsing helpers."""

from __future__ import annotations

from typing import Any

from openminion.base.config.base import ConfigError
from openminion.base.config.parse import (
    _as_bool,
    _as_float,
    _as_int,
    _normalize_identity_budget_truncate_strategy,
)
from openminion.base.config.runtime import (
    IdentityBudgetCompactionConfig,
    IdentityBudgetConfig,
)

_DEFAULT_IDENTITY_BUDGET_CAP_RATIOS: dict[str, float] = {
    "constraints": 0.40,
    "tool_posture": 0.30,
    "mission": 0.40,
    "responsibilities": 0.30,
    "voice": 0.20,
    "notes": 0.10,
}


def _normalize_identity_budget_section_name(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip().lower()
    return str(raw).strip().lower()


def _derive_default_identity_section_caps(
    section_name: str,
    token_budget: int | None = None,
) -> int:
    total_tokens = max(1, token_budget or 200)
    ratio = _DEFAULT_IDENTITY_BUDGET_CAP_RATIOS.get(section_name, 0.10)
    return max(1, int(total_tokens * ratio))


def _parse_identity_budget_config(
    value: Any,
    *,
    fallback_total_tokens: int = 200,
) -> IdentityBudgetConfig:
    if value is None:
        return IdentityBudgetConfig(total_tokens=max(1, int(fallback_total_tokens)))
    if not isinstance(value, dict):
        raise ConfigError("context.budget must be an object")

    total_tokens = max(
        1,
        _as_int(value.get("total_tokens"), max(1, int(fallback_total_tokens))),
    )

    section_order_raw = value.get("section_order")
    if section_order_raw is not None and not isinstance(section_order_raw, list):
        raise ConfigError("context.budget.section_order must be a list")
    if isinstance(section_order_raw, list):
        section_order = [
            _normalize_identity_budget_section_name(item)
            for item in section_order_raw
            if _normalize_identity_budget_section_name(item)
        ]
        if not section_order:
            raise ConfigError("context.budget.section_order cannot be empty")
    else:
        section_order = list(IdentityBudgetConfig().section_order)

    section_priority_raw = value.get("section_priority")
    if section_priority_raw is not None and not isinstance(section_priority_raw, dict):
        raise ConfigError("context.budget.section_priority must be an object")
    section_priority = {
        _normalize_identity_budget_section_name(key): _as_int(priority, 0)
        for key, priority in (section_priority_raw or {}).items()
        if _normalize_identity_budget_section_name(key)
    }

    section_caps_raw = value.get("section_caps")
    if section_caps_raw is not None and not isinstance(section_caps_raw, dict):
        raise ConfigError("context.budget.section_caps must be an object")
    section_caps = {
        _normalize_identity_budget_section_name(key): max(1, _as_int(cap, 1))
        for key, cap in (section_caps_raw or {}).items()
        if _normalize_identity_budget_section_name(key)
    }
    for section_name in section_order:
        section_caps.setdefault(
            section_name,
            _derive_default_identity_section_caps(section_name, total_tokens),
        )

    truncate_strategy = _normalize_identity_budget_truncate_strategy(
        value.get("truncate_strategy")
    )

    compaction_raw = value.get("compaction")
    if compaction_raw is not None and not isinstance(compaction_raw, dict):
        raise ConfigError("context.budget.compaction must be an object")
    compaction_payload = compaction_raw or {}
    compaction = IdentityBudgetCompactionConfig(
        enabled=_as_bool(compaction_payload.get("enabled"), False),
        provider=str(compaction_payload.get("provider", "")),
        model=str(compaction_payload.get("model", "")),
        temperature=_as_float(compaction_payload.get("temperature"), 0.0),
        max_tokens=max(1, _as_int(compaction_payload.get("max_tokens"), 120)),
    )

    return IdentityBudgetConfig(
        total_tokens=total_tokens,
        section_order=section_order,
        section_priority=section_priority,
        section_caps=section_caps,
        truncate_strategy=truncate_strategy,
        compaction=compaction,
    )


__all__ = [
    "_normalize_identity_budget_section_name",
    "_derive_default_identity_section_caps",
    "_parse_identity_budget_config",
]
