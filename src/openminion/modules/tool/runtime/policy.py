from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal environments

    class _YamlFallback:
        @staticmethod
        def safe_load(raw: str) -> Any:
            import json

            try:
                return json.loads(raw)
            except ValueError:
                return {}

    yaml = _YamlFallback()  # type: ignore[assignment]

from .policy_access import PolicyAccessMixin
from .policy_binding import (
    RuntimeBindingPolicy,
    ToolBindingPolicyManager,
    reorder_runtime_chain,
)
from .policy_config import PolicyConfigMixin
from .policy_defaults import DEFAULT_POLICY
from .policy_exec import PolicyExecMixin
from .policy_normalization import (
    canonical_tool_name,
    deep_merge as _deep_merge,
    normalize_policy_legacy_aliases as _normalize_policy_legacy_aliases,
)
from .policy_shared import SCOPE_ORDER, _invalid_argument


@dataclass
class Policy(PolicyExecMixin, PolicyAccessMixin, PolicyConfigMixin):
    raw: dict[str, Any]

    @staticmethod
    def load(path: Path) -> "Policy":
        parsed: dict[str, Any] = {}
        if path.exists():
            loaded = yaml.safe_load(path.read_text()) or {}
            if not isinstance(loaded, dict):
                raise _invalid_argument("Policy file must parse to an object")
            parsed = _normalize_policy_legacy_aliases(loaded)
        merged = _deep_merge(DEFAULT_POLICY, parsed)
        return Policy(raw=merged)


__all__ = [
    "DEFAULT_POLICY",
    "Policy",
    "RuntimeBindingPolicy",
    "SCOPE_ORDER",
    "ToolBindingPolicyManager",
    "canonical_tool_name",
    "reorder_runtime_chain",
]
