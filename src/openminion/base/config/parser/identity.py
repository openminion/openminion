"""Context and identity config parsing helpers."""

from __future__ import annotations

from typing import Any

from openminion.base.config.core import OpenMinionConfig
from openminion.base.config.parse import (
    _as_int,
    _normalize_identity_budget_truncate_strategy,
)
from .budget import _parse_identity_budget_config
from openminion.base.config.runtime import ContextConfig, IdentityConfig


def _build_context_config(context_payload: dict[str, Any]) -> ContextConfig:
    identity_budget_payload = context_payload.get("identity_budget")
    return ContextConfig(
        identity_budget=_parse_identity_budget_config(
            identity_budget_payload,
            fallback_total_tokens=max(
                1,
                _as_int(context_payload.get("identity_tokens"), 200),
            ),
        )
        if identity_budget_payload is not None
        else None,
    )


def _build_identity_config(identity_payload: dict[str, Any]) -> IdentityConfig:
    root = str(identity_payload.get("root", "")).strip()
    return IdentityConfig(
        db_path=str(identity_payload.get("db_path", "")).strip() or root,
        bundle_root=str(identity_payload.get("bundle_root", "")).strip() or root,
        root=root,
    )


def _identity_context_to_payload(config: OpenMinionConfig) -> dict[str, Any]:
    budget = getattr(
        config.context,
        "identity_budget",
        getattr(config.context, "budget", None),
    )
    return {
        "context": {
            "identity_budget": (
                {
                    "total_tokens": budget.total_tokens,
                    "section_order": list(budget.section_order),
                    "section_priority": dict(budget.section_priority),
                    "section_caps": dict(budget.section_caps),
                    "truncate_strategy": _normalize_identity_budget_truncate_strategy(
                        budget.truncate_strategy
                    ),
                    "compaction": {
                        "enabled": bool(budget.compaction.enabled),
                        "provider": budget.compaction.provider,
                        "model": budget.compaction.model,
                        "temperature": budget.compaction.temperature,
                        "max_tokens": budget.compaction.max_tokens,
                    },
                }
                if budget is not None
                else None
            )
        },
        "identity": {
            "db_path": config.identity.db_path,
            "bundle_root": config.identity.bundle_root,
            "root": config.identity.root,
        },
    }
