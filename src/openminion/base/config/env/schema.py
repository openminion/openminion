"""Validation helpers for runtime and provider environment variables."""

from __future__ import annotations

from dataclasses import dataclass, field

from openminion.base.config.core import OpenMinionConfig
from openminion.base.config.env import EnvironmentConfig
from openminion.base.config.env.registry import iter_deprecated_env_specs

_KEYLESS_PROVIDERS = frozenset({"echo", "ollama", "cortensor"})
_PROVIDER_KEY_DEFAULTS = {
    "openai": ("openai", "OPENAI_API_KEY"),
    "anthropic": ("anthropic", "ANTHROPIC_API_KEY"),
    "openrouter": ("openrouter", "OPENROUTER_API_KEY"),
    "cerebras": ("cerebras", "CEREBRAS_API_KEY"),
    "groq": ("groq", "GROQ_API_KEY"),
}


@dataclass(frozen=True)
class EnvValidationResult:
    ok: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    required_vars: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "required_vars": list(self.required_vars),
        }


def validate_runtime_core(env: EnvironmentConfig) -> EnvValidationResult:
    warnings: list[str] = []

    if env.has("OPENMINION_DATA_ROOT_ENFORCEMENT"):
        raw = env.get("OPENMINION_DATA_ROOT_ENFORCEMENT", "").strip().lower()
        if raw not in {"hard", "soft", "warn"}:
            warnings.append(
                "OPENMINION_DATA_ROOT_ENFORCEMENT should be one of hard|soft|warn."
            )

    turn_timeout = env.openminion_turn_timeout_seconds
    if turn_timeout < 0:
        warnings.append("OPENMINION_TURN_TIMEOUT_SECONDS should be >= 0.")

    for spec in iter_deprecated_env_specs():
        if env.has(spec.name):
            guidance = spec.deprecation_guidance or (
                f"Use {spec.replacement} instead." if spec.replacement else ""
            )
            suffix = f" {guidance}" if guidance else ""
            warnings.append(f"{spec.name} is deprecated.{suffix}")

    return EnvValidationResult(ok=True, warnings=tuple(warnings))


def validate_for_provider(
    *,
    provider_name: str,
    env: EnvironmentConfig,
    config: OpenMinionConfig | None = None,
) -> EnvValidationResult:
    provider = str(provider_name or "").strip().lower() or "echo"
    provider = "anthropic" if provider == "claude" else provider

    if provider in _KEYLESS_PROVIDERS:
        return EnvValidationResult(ok=True)

    required_name = _required_provider_env_name(provider, config)
    if required_name is None:
        return EnvValidationResult(
            ok=False,
            errors=(f"Unknown provider '{provider}'.",),
        )
    required = [required_name] if required_name else []
    errors: list[str] = []

    for key in required:
        if not env.has(key):
            errors.append(
                f"{provider} provider requires env var {key} (or provider api_key in config)."
            )

    return EnvValidationResult(
        ok=not errors,
        errors=tuple(errors),
        required_vars=tuple(required),
    )


def _provider_env_name(
    *,
    config_value: str,
    configured_env: str,
    default_env: str,
) -> str:
    if str(config_value or "").strip():
        return ""
    return str(configured_env or "").strip() or default_env


def _required_provider_env_name(
    provider: str,
    config: OpenMinionConfig | None,
) -> str | None:
    provider_spec = _PROVIDER_KEY_DEFAULTS.get(provider)
    if provider_spec is None:
        return None
    provider_attr, default_env = provider_spec
    provider_config = getattr(config.providers, provider_attr) if config else None
    return _provider_env_name(
        config_value=(provider_config.api_key if provider_config else ""),
        configured_env=(provider_config.api_key_env if provider_config else ""),
        default_env=default_env,
    )


__all__ = [
    "EnvValidationResult",
    "validate_for_provider",
    "validate_runtime_core",
]
