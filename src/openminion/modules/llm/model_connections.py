"""Configured model-connection facts shared by setup and runtime APIs."""

from typing import Any, Mapping

from openminion.base.config import AgentProfileConfig, OpenMinionConfig

from .config import resolve_provider_identity_translation
from .setup_catalog import ProviderSetupPreset

_PROVIDER_ALIASES = {"claude": "anthropic"}


def canonical_provider_name(provider: str) -> str:
    normalized = provider.strip().lower()
    return _PROVIDER_ALIASES.get(normalized, normalized)


def configured_model(
    config: OpenMinionConfig,
    *,
    preset: ProviderSetupPreset,
    agent_id: str,
) -> str:
    provider_config = getattr(config.providers, preset.runtime_adapter, None)
    if provider_config is None:
        return ""
    model = str(getattr(provider_config, "model", "") or "").strip()
    base_url = str(getattr(provider_config, "base_url", "") or "").strip()
    identity = dict(getattr(provider_config, "provider_identity", {}) or {})
    profile = config.agents.get(agent_id)
    if profile is not None:
        if canonical_provider_name(profile.provider) != canonical_provider_name(
            preset.runtime_adapter
        ):
            return ""
        overrides = profile.provider_config_overrides
        model = str(overrides.get("model", model) or "").strip()
        base_url = str(overrides.get("base_url", base_url) or "").strip()
        identity = dict(overrides.get("provider_identity", identity) or {})
    if not model or preset.requires_base_url:
        return ""
    if preset.runtime_adapter != "openai":
        return model
    configured = identity or resolve_provider_identity_translation(
        "openai", model=model, base_url=base_url
    )
    expected = resolve_provider_identity_translation(
        "openai",
        model=preset.recommended_models[0],
        base_url=preset.default_base_url,
    )
    if configured.get("service_vendor") != expected.get("service_vendor"):
        return ""
    if expected.get("service_vendor") == "openai" and (
        base_url.rstrip("/").lower() != preset.default_base_url.rstrip("/").lower()
    ):
        return ""
    return model


def legacy_model_connection(
    config: OpenMinionConfig,
    profile: AgentProfileConfig,
) -> tuple[str, dict[str, Any]] | None:
    provider = canonical_provider_name(profile.provider)
    provider_config = getattr(config.providers, provider, None)
    route = dict(profile.provider_config_overrides)
    model = str(route.get("model", getattr(provider_config, "model", "")) or "").strip()
    if not provider or not model:
        return None
    base_url = str(
        route.get("base_url", getattr(provider_config, "base_url", "")) or ""
    ).strip()
    identity = route.get("provider_identity") or getattr(
        provider_config, "provider_identity", None
    )
    resolved = identity or resolve_provider_identity_translation(
        provider, model=model, base_url=base_url
    )
    connection_id = str(resolved.get("service_vendor") or provider).strip()
    route.pop("model", None)
    return connection_id, {
        "provider": provider,
        "display_name": connection_id,
        "models": [model],
        "provider_config_overrides": route,
        "default": True,
    }


def add_model_connection(
    profile: AgentProfileConfig,
    *,
    connection_id: str,
    display_name: str,
    provider: str,
    model: str,
    provider_patch: Mapping[str, Any],
    default: bool,
) -> None:
    existing = profile.model_connections.get(connection_id, {})
    models = list(existing.get("models", []))
    if model not in models:
        models.append(model)
    route = dict(provider_patch)
    route.pop("model", None)
    profile.model_connections[connection_id] = {
        "provider": provider,
        "display_name": display_name,
        "models": models,
        "provider_config_overrides": route,
        "default": default or bool(existing.get("default")),
    }
