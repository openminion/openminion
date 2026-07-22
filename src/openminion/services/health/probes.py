from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable, Iterable

from openminion.base.config.env import resolve_environment_config
from openminion.modules.storage.runtime.context import build_runtime_storage


@dataclass
class ProbeResult:
    id: str
    status: str
    message: str
    details: dict[str, Any] | None = None
    remediation: str = ""


@dataclass
class StorageProbeResult:
    probe: ProbeResult
    runtime_storage: Any | None = None


def probe_config_exists(config_path: Path) -> ProbeResult:
    if config_path.exists():
        return ProbeResult(
            id="config.exists",
            status="ok",
            message=f"Config file found at {config_path}",
        )
    return ProbeResult(
        id="config.exists",
        status="warn",
        message=f"Config file not found at {config_path}; using defaults",
        remediation="Run `openminion config init` to create and persist config.",
    )


def probe_storage_ready(
    storage_path: Path, *, keep_open: bool = False
) -> StorageProbeResult:
    runtime_storage = None
    try:
        runtime_storage = build_runtime_storage(storage_path)
        migration_result = runtime_storage.migration_result
        probe = ProbeResult(
            id="storage.ready",
            status="ok",
            message=f"Storage schema is ready at {storage_path}",
            details={
                "path": str(storage_path),
                "current_version": migration_result.current_version,
                "applied_versions": list(migration_result.applied_versions),
            },
        )
        if keep_open:
            return StorageProbeResult(probe=probe, runtime_storage=runtime_storage)
        runtime_storage.close()
        return StorageProbeResult(probe=probe, runtime_storage=None)
    except Exception as exc:
        if runtime_storage is not None:
            try:
                runtime_storage.close()
            except Exception:
                pass
        return StorageProbeResult(
            probe=ProbeResult(
                id="storage.ready",
                status="fail",
                message=f"Storage initialization failed: {exc}",
                details={"path": str(storage_path)},
                remediation="Fix sqlite path/permissions or migration state and re-run doctor.",
            ),
            runtime_storage=None,
        )


def probe_provider_supported(
    *,
    provider_name: str,
    supported_providers: Iterable[str],
) -> ProbeResult:
    supported = set(str(item).strip().lower() for item in supported_providers)
    if provider_name in supported:
        return ProbeResult(
            id="provider.supported",
            status="ok",
            message=f"Configured provider '{provider_name}' is supported",
        )
    supported_sorted = sorted(item for item in supported if item)
    return ProbeResult(
        id="provider.supported",
        status="fail",
        message=f"Configured provider '{provider_name}' is not supported",
        details={"supported": supported_sorted},
        remediation=(
            "Set `agent.provider` to one of: " + ", ".join(supported_sorted) + "."
            if supported_sorted
            else ""
        ),
    )


def probe_provider_key(config: Any, provider_name: str) -> Optional[ProbeResult]:
    if provider_name == "openai":
        return _build_key_probe(
            provider_name="openai",
            config_key=config.providers.openai.api_key,
            env_name=config.providers.openai.api_key_env,
            default_env="OPENAI_API_KEY",
        )
    if provider_name in {"anthropic", "claude"}:
        return _build_key_probe(
            provider_name="anthropic",
            config_key=config.providers.anthropic.api_key,
            env_name=config.providers.anthropic.api_key_env,
            default_env="ANTHROPIC_API_KEY",
        )
    if provider_name == "openrouter":
        return _build_key_probe(
            provider_name="openrouter",
            config_key=config.providers.openrouter.api_key,
            env_name=config.providers.openrouter.api_key_env,
            default_env="OPENROUTER_API_KEY",
        )
    if provider_name == "cerebras":
        return _build_key_probe(
            provider_name="cerebras",
            config_key=config.providers.cerebras.api_key,
            env_name=config.providers.cerebras.api_key_env,
            default_env="CEREBRAS_API_KEY",
        )
    if provider_name == "groq":
        return _build_key_probe(
            provider_name="groq",
            config_key=config.providers.groq.api_key,
            env_name=config.providers.groq.api_key_env,
            default_env="GROQ_API_KEY",
        )
    if provider_name == "cortensor":
        key_source = _resolve_key_source(
            config.providers.cortensor.api_key,
            config.providers.cortensor.api_key_env,
            "CORTENSOR_API_KEY",
        )
        if key_source is None:
            return ProbeResult(
                id="provider.cortensor.key",
                status="warn",
                message=(
                    "Cortensor provider has no API key configured; this is valid for local router "
                    "nodes without auth."
                ),
            )
        return ProbeResult(
            id="provider.cortensor.key",
            status="ok",
            message=f"Cortensor API key is configured via {key_source}",
        )
    return None


def probe_provider_session(config: Any, provider_name: str) -> Optional[ProbeResult]:
    if provider_name != "cortensor":
        return None
    if not _cortensor_completion_mode(
        api_mode=config.providers.cortensor.api_mode,
        base_url=config.providers.cortensor.base_url,
    ):
        return None
    session_ids = _resolve_cortensor_session_candidates(config)
    if not session_ids:
        return ProbeResult(
            id="provider.cortensor.session_id",
            status="fail",
            message=(
                "Cortensor completion mode requires at least one valid session id "
                "(`providers.cortensor.session_id`, `providers.cortensor.session_ids`, "
                "`providers.cortensor.dedicated_session_ids`, or "
                "`providers.cortensor.ephemeral_session_ids`)."
            ),
            remediation=(
                "Set one or more Cortensor session lists "
                "(`session_id`, `session_ids`, `dedicated_session_ids`, `ephemeral_session_ids`) "
                "to valid pre-created session ids."
            ),
        )
    return ProbeResult(
        id="provider.cortensor.session_id",
        status="ok",
        message=(
            "Cortensor completion session ids are configured "
            f"({', '.join(str(item) for item in session_ids)})"
        ),
    )


def probe_channels_enabled(enabled_channels: Iterable[str]) -> ProbeResult:
    normalized_channels = [str(item) for item in enabled_channels]
    if normalized_channels:
        return ProbeResult(
            id="channels.enabled",
            status="ok",
            message="Enabled channels list is present",
            details={"enabled_channels": normalized_channels},
        )
    return ProbeResult(
        id="channels.enabled",
        status="fail",
        message="No enabled channels configured",
        remediation="Set `enabled_channels` with at least one channel (e.g. `console`).",
    )


def probe_default_channel_in_enabled(
    *,
    default_channel: str,
    enabled_channels: Iterable[str],
) -> ProbeResult:
    normalized_channels = [str(item) for item in enabled_channels]
    if default_channel in normalized_channels:
        return ProbeResult(
            id="channels.default_in_enabled",
            status="ok",
            message=f"Default channel '{default_channel}' is enabled",
        )
    return ProbeResult(
        id="channels.default_in_enabled",
        status="warn",
        message=f"Default channel '{default_channel}' is not in enabled_channels",
        remediation="Add default channel to `enabled_channels` for consistent routing.",
    )


def probe_plugins_enabled(enabled_plugins: Iterable[str]) -> ProbeResult:
    normalized_plugins = [str(item) for item in enabled_plugins]
    if normalized_plugins:
        return ProbeResult(
            id="plugins.enabled",
            status="ok",
            message="Enabled plugins list is present",
            details={"enabled_plugins": normalized_plugins},
        )
    return ProbeResult(
        id="plugins.enabled",
        status="warn",
        message="No plugins enabled",
        remediation="Enable at least the `validate` plugin for baseline diagnostics.",
    )


def probe_runtime_bootstrap(
    *,
    bootstrap_fn: Callable[[], dict[str, Any]],
    success_message: str,
    failure_remediation: str = "",
    failure_message_prefix: str = "Runtime bootstrap failed",
) -> ProbeResult:
    try:
        details = bootstrap_fn()
        return ProbeResult(
            id="runtime.bootstrap",
            status="ok",
            message=success_message,
            details=details,
        )
    except Exception as exc:
        return ProbeResult(
            id="runtime.bootstrap",
            status="fail",
            message=f"{failure_message_prefix}: {exc}",
            remediation=failure_remediation,
        )


def _build_key_probe(
    *,
    provider_name: str,
    config_key: str,
    env_name: str,
    default_env: str,
) -> ProbeResult:
    provider_display_names = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "openrouter": "OpenRouter",
        "cerebras": "Cerebras",
        "groq": "Groq",
    }
    display_name = provider_display_names.get(provider_name, provider_name.capitalize())
    key_source = _resolve_key_source(config_key, env_name, default_env)
    if key_source is None:
        return ProbeResult(
            id=f"provider.{provider_name}.key",
            status="fail",
            message=f"{display_name} provider is configured but API key is missing",
            remediation=(
                f"Set `providers.{provider_name}.api_key` in config or export {env_name}."
            ),
        )
    return ProbeResult(
        id=f"provider.{provider_name}.key",
        status="ok",
        message=f"{display_name} API key is configured via {key_source}",
    )


def _resolve_key_source(
    config_key: str, env_name: str, default_env: str
) -> Optional[str]:
    if str(config_key or "").strip():
        return "config"
    env_key = str(env_name or "").strip() or default_env
    if resolve_environment_config().get(env_key, "").strip():
        return f"env:{env_key}"
    return None


def _resolve_cortensor_session_candidates(config: Any) -> list[int]:
    seen: set[int] = set()
    results: list[int] = []
    for raw_value in [
        config.providers.cortensor.session_id,
        *list(config.providers.cortensor.session_ids),
        *list(config.providers.cortensor.dedicated_session_ids),
        *list(config.providers.cortensor.ephemeral_session_ids),
    ]:
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            continue
        if parsed <= 0 or parsed in seen:
            continue
        seen.add(parsed)
        results.append(parsed)
    return results


def _cortensor_completion_mode(*, api_mode: str, base_url: str) -> bool:
    mode = (api_mode or "").strip().lower()
    if mode == "cortensor_completion":
        return True
    if mode == "openai_chat":
        return False
    return base_url.strip().lower().endswith("/completions")
