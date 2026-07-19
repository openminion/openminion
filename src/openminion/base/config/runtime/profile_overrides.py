"""Invocation-level runtime profile override contracts and parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from openminion.base.config.base import ConfigError

_PROVIDER_CONFIG_FIELDS = {
    name: name
    for name in "anthropic cerebras cortensor groq ollama openai openrouter".split()
}
_PROVIDER_CONFIG_FIELDS.update(claude="anthropic", echo="")
_UNSUPPORTED_OVERRIDE_FIELDS = {
    "override-agent-name": "Runtime profiles cannot rename agents.",
    "override-identity": "Identity overrides belong to the identity owner.",
    "override-tool-policy": "Tool-policy overrides belong to the policy owner.",
}

PERMISSION_MODE_CYCLE = ("default", "readonly", "bypass")
PERMISSION_MODE_DEFAULT, PERMISSION_MODE_READONLY, PERMISSION_MODE_BYPASS = (
    PERMISSION_MODE_CYCLE
)
PERMISSION_MODE_VALUES = frozenset(PERMISSION_MODE_CYCLE)


def next_permission_mode(current: str) -> str:
    normalized = str(current or "").strip().lower()
    if normalized not in PERMISSION_MODE_VALUES:
        return PERMISSION_MODE_DEFAULT
    index = (PERMISSION_MODE_CYCLE.index(normalized) + 1) % len(PERMISSION_MODE_CYCLE)
    return PERMISSION_MODE_CYCLE[index]


@dataclass(frozen=True)
class RunProfileOverrides:
    provider: str = ""
    model: str = ""
    system_prompt: str = ""
    thinking: str = ""
    permission_mode: str = ""
    permission_overrides: tuple[tuple[str, str], ...] = ()

    def is_empty(self) -> bool:
        return not any(
            getattr(self, name)
            for name in (
                "provider model system_prompt thinking permission_mode "
                "permission_overrides"
            ).split()
        )

    def cache_key(self) -> str:
        if self.is_empty():
            return "none"
        values = (
            self.provider,
            self.model,
            self.system_prompt,
            self.thinking,
            self.permission_mode,
            ",".join(f"{tool}:{mode}" for tool, mode in self.permission_overrides)
            or "-",
        )
        return "|".join(value or "-" for value in values)


def combine_run_profile_overrides(
    base: RunProfileOverrides | None,
    extra: RunProfileOverrides | None,
) -> RunProfileOverrides:
    base = base or RunProfileOverrides()
    extra = extra or RunProfileOverrides()
    return RunProfileOverrides(
        provider=extra.provider or base.provider,
        model=extra.model or base.model,
        system_prompt=extra.system_prompt or base.system_prompt,
        thinking=extra.thinking or base.thinking,
        permission_mode=extra.permission_mode or base.permission_mode,
        permission_overrides=extra.permission_overrides or base.permission_overrides,
    )


def _override(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value).strip()
    return ""


def _reject_unsupported_override_keys(payload: Mapping[str, Any]) -> None:
    for key, reason in _UNSUPPORTED_OVERRIDE_FIELDS.items():
        if _override(payload, key, key.replace("-", "_")):
            raise ConfigError(f"Unsupported runtime override {key!r}: {reason}")


def _normalized_permission_overrides(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    payload = value
    if isinstance(value, str):
        try:
            payload = json.loads(value) if value.strip() else {}
        except json.JSONDecodeError as exc:
            raise ConfigError("permission_overrides must be a JSON object") from exc
    if not isinstance(payload, Mapping):
        raise ConfigError("permission_overrides must map tool names to modes")
    aliases = {
        "default": "ask",
        "plan": "ask",
        "acceptedits": "auto",
        "bypasspermissions": "bypass",
        "read_only": "readonly",
        "read-only": "readonly",
    }
    valid = {"ask", "auto", "bypass", "readonly"}
    normalized: dict[str, str] = {}
    for raw_name, raw_mode in payload.items():
        name = str(raw_name or "").strip().lower()
        if not name:
            continue
        raw_mode_token = str(raw_mode or "").strip().lower()
        mode = aliases.get(raw_mode_token, raw_mode_token)
        if mode not in valid:
            raise ConfigError(f"Unsupported permission mode {raw_mode!r} for {name!r}")
        normalized[name] = mode
    return tuple(sorted(normalized.items()))


def _provider_config_field_name(provider_name: str) -> str:
    normalized = str(provider_name or "").strip().lower()
    if normalized not in _PROVIDER_CONFIG_FIELDS:
        valid = ", ".join(repr(name) for name in sorted(_PROVIDER_CONFIG_FIELDS))
        raise ConfigError(
            f"Unknown provider {provider_name!r}; expected one of {valid}"
        )
    return _PROVIDER_CONFIG_FIELDS[normalized]


def run_profile_overrides_from_mapping(
    payload: Mapping[str, Any] | None,
) -> RunProfileOverrides:
    if payload is None:
        return RunProfileOverrides()
    _reject_unsupported_override_keys(payload)
    provider = _override(payload, "override_provider", "override-provider")
    permission_mode = _override(
        payload,
        *"permission_mode permission-mode override_permission_mode override-permission-mode".split(),
    ).lower()
    if permission_mode not in PERMISSION_MODE_VALUES | {""}:
        raise ConfigError(f"Unsupported permission mode {permission_mode!r}")
    permission_keys = (
        "permission_overrides permission-overrides "
        "override_permission_overrides override-permission-overrides"
    ).split()
    permission_overrides = next(
        (payload[key] for key in permission_keys if payload.get(key)),
        None,
    )
    if provider:
        _provider_config_field_name(provider)
    return RunProfileOverrides(
        provider=provider,
        model=_override(payload, "override_model", "override-model"),
        system_prompt=_override(
            payload, "override_system_prompt", "override-system-prompt"
        ),
        thinking=_override(payload, "override_thinking", "override-thinking"),
        permission_mode=""
        if permission_mode == PERMISSION_MODE_DEFAULT
        else permission_mode,
        permission_overrides=_normalized_permission_overrides(permission_overrides),
    )
