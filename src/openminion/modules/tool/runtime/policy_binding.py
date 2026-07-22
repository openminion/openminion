from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Iterable, Mapping, Sequence

from .policy_normalization import dedupe as _dedupe, dedupe_normalized as _dedupe_normalized


def reorder_runtime_chain(
    *,
    runtime_binding_id: str,
    default_chain: Iterable[str],
    runtime_binding_policies: dict[str, Any] | None,
    available_tool_names: Iterable[str] | None = None,
) -> tuple[str, ...]:
    manager = ToolBindingPolicyManager.from_runtime_binding_policy_payload(
        runtime_binding_policies
    )
    return manager.reorder_runtime_chain(
        runtime_binding_id=runtime_binding_id,
        default_chain=tuple(default_chain),
        available_tool_names=tuple(available_tool_names)
        if available_tool_names is not None
        else None,
    )

@dataclass(frozen=True)
class RuntimeBindingPolicy:
    runtime_binding_id: str
    primary: str
    fallback_tools: tuple[str, ...]

class ToolBindingPolicyManager:
    def __init__(
        self,
        *,
        policies: Mapping[str, RuntimeBindingPolicy] | None = None,
        selection_strategy: str = "ordered",
        fallback_on: Sequence[str] = (),
        no_fallback_on: Sequence[str] = (),
    ) -> None:
        self._policies = dict(policies or {})
        self._selection_strategy = (
            str(selection_strategy or "ordered").strip() or "ordered"
        )
        self._fallback_on = tuple(_dedupe_normalized(fallback_on))
        self._no_fallback_on = tuple(_dedupe_normalized(no_fallback_on))

    @classmethod
    def from_tool_selection_config(cls, config: Any) -> "ToolBindingPolicyManager":
        return cls.from_tool_selection_config_with_defaults(config)

    @classmethod
    def from_tool_selection_config_with_defaults(
        cls,
        config: Any,
        *,
        default_policies: Mapping[str, RuntimeBindingPolicy] | None = None,
    ) -> "ToolBindingPolicyManager":
        runtime_bindings = getattr(config, "runtime_bindings", {}) or {}
        parsed: dict[str, RuntimeBindingPolicy] = dict(default_policies or {})
        for runtime_binding_id, binding in runtime_bindings.items():
            binding_id = str(runtime_binding_id or "").strip()
            if not binding_id:
                continue
            primary = str(getattr(binding, "primary", "") or "").strip()
            fallback_tools = [
                str(item).strip()
                for item in (getattr(binding, "fallback_tools", []) or [])
                if str(item).strip()
            ]
            parsed[binding_id] = RuntimeBindingPolicy(
                runtime_binding_id=binding_id,
                primary=primary,
                fallback_tools=tuple(_dedupe(fallback_tools)),
            )
        return cls(
            policies=parsed,
            selection_strategy=str(
                getattr(config, "runtime_binding_selection_strategy", "ordered")
                or "ordered"
            ),
            fallback_on=getattr(config, "runtime_fallback_on", ()) or (),
            no_fallback_on=getattr(config, "runtime_no_fallback_on", ()) or (),
        )

    @staticmethod
    def default_policy(
        runtime_binding_id: str, candidates: Sequence[str]
    ) -> RuntimeBindingPolicy | None:
        binding_id = str(runtime_binding_id or "").strip()
        ordered = tuple(_dedupe(candidates))
        if not binding_id or not ordered:
            return None
        return RuntimeBindingPolicy(
            runtime_binding_id=binding_id,
            primary=ordered[0],
            fallback_tools=ordered[1:],
        )

    @classmethod
    def from_runtime_binding_policy_payload(
        cls,
        payload: Mapping[str, Any] | None,
    ) -> "ToolBindingPolicyManager":
        parsed: dict[str, RuntimeBindingPolicy] = {}
        source = payload or {}
        policies_raw = source.get("runtime_binding_policies")
        if not isinstance(policies_raw, Mapping):
            policies_raw = source

        for runtime_binding_id, raw_policy in policies_raw.items():
            binding_id = str(runtime_binding_id or "").strip()
            if not binding_id:
                continue
            if isinstance(raw_policy, RuntimeBindingPolicy):
                parsed[binding_id] = raw_policy
                continue
            if not isinstance(raw_policy, Mapping):
                continue

            primary = str(raw_policy.get("primary", "") or "").strip()
            fallback_raw = raw_policy.get("fallback_tools", ())
            if isinstance(fallback_raw, str):
                fallback_items = [
                    item.strip() for item in fallback_raw.split(",") if item.strip()
                ]
            elif isinstance(fallback_raw, Sequence):
                fallback_items = [
                    str(item).strip() for item in fallback_raw if str(item).strip()
                ]
            else:
                fallback_items = []

            parsed[binding_id] = RuntimeBindingPolicy(
                runtime_binding_id=binding_id,
                primary=primary,
                fallback_tools=tuple(_dedupe(fallback_items)),
            )

        selection_strategy = (
            str(
                source.get("runtime_binding_selection_strategy", "ordered") or "ordered"
            ).strip()
            or "ordered"
        )
        fallback_on = source.get("runtime_fallback_on", ())
        no_fallback_on = source.get("runtime_no_fallback_on", ())
        return cls(
            policies=parsed,
            selection_strategy=selection_strategy,
            fallback_on=fallback_on if isinstance(fallback_on, Sequence) else (),
            no_fallback_on=no_fallback_on
            if isinstance(no_fallback_on, Sequence)
            else (),
        )

    def policy_for(self, runtime_binding_id: str) -> RuntimeBindingPolicy | None:
        binding_id = str(runtime_binding_id or "").strip()
        if not binding_id:
            return None
        return self._policies.get(binding_id)

    def runtime_binding_policies_payload(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for binding_id in sorted(self._policies.keys()):
            policy = self._policies[binding_id]
            out[binding_id] = {
                "primary": policy.primary,
                "fallback_tools": list(policy.fallback_tools),
            }
        return out

    def metadata_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        policies = self.runtime_binding_policies_payload()
        if policies:
            payload["runtime_binding_policies"] = policies
        if self._selection_strategy:
            payload["runtime_binding_selection_strategy"] = self._selection_strategy
        if self._fallback_on:
            payload["runtime_fallback_on"] = list(self._fallback_on)
        if self._no_fallback_on:
            payload["runtime_no_fallback_on"] = list(self._no_fallback_on)
        return payload

    def reorder_runtime_chain(
        self,
        *,
        runtime_binding_id: str,
        default_chain: Sequence[str],
        available_tool_names: Sequence[str] | None = None,
    ) -> tuple[str, ...]:
        binding_id = str(runtime_binding_id or "").strip()
        if not binding_id:
            return tuple(_dedupe(default_chain))

        available: set[str] | None = None
        default_items = [
            str(item).strip() for item in default_chain if str(item).strip()
        ]
        if available_tool_names is not None:
            available = {
                str(item).strip() for item in available_tool_names if str(item).strip()
            }
            default_items = [item for item in default_items if item in available]

        policy = self._policies.get(binding_id)
        if policy is None:
            return tuple(_dedupe(default_items))

        preferred = [policy.primary, *policy.fallback_tools]
        ordered: list[str] = []
        seen: set[str] = set()

        for candidate in preferred:
            token = str(candidate or "").strip()
            if not token or token in seen:
                continue
            if available is not None and token not in available:
                continue
            ordered.append(token)
            seen.add(token)

        for candidate in default_items:
            if candidate in seen:
                continue
            ordered.append(candidate)
            seen.add(candidate)
        return tuple(ordered)

    def should_fallback(self, *, error_text: str) -> bool:
        text = str(error_text or "").strip().lower()
        if not text:
            return False
        if self._no_fallback_on and any(
            token in text for token in self._no_fallback_on
        ):
            return False
        if self._fallback_on and any(token in text for token in self._fallback_on):
            return True
        return False


__all__ = ["RuntimeBindingPolicy", "ToolBindingPolicyManager", "reorder_runtime_chain"]
