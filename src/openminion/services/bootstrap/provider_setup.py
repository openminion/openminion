"""Provider/model first-run setup composition over existing config owners."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
from urllib.parse import urlparse

from openminion.base.config import AgentProfileConfig, OpenMinionConfig
from openminion.base.config.env import resolve_environment_config
from openminion.base.config.io import resolve_config_path
from openminion.base.config.runtime.profile import build_runtime_config
from openminion.modules.llm.setup_catalog import (
    ModelChoiceResult,
    ProviderSetupPreset,
    get_setup_preset,
    resolve_model_choice,
)
from openminion.modules.llm.config import resolve_provider_identity_translation


_MANAGED_PROVIDER_OVERRIDE_KEYS = frozenset(
    {
        "api_key",
        "api_key_env",
        "base_url",
        "model",
        "provider_identity",
    }
)
_PROVIDER_ALIASES = {
    "claude": "anthropic",
}


class ProviderSetupError(ValueError):
    """Raised when first-run setup cannot safely produce a config."""


@dataclass(frozen=True)
class CredentialResolution:
    env_var: str
    source: str
    local_value: str = ""

    @property
    def has_credential(self) -> bool:
        return bool(self.local_value) or self.source == "env"


@dataclass(frozen=True)
class ProviderSetupRequest:
    preset_id: str
    agent_id: str
    model: str = ""
    base_url: str = ""
    stored_api_key: str = ""
    allow_local_api_key: bool = False
    config_path: str | None = None
    home_root: Path | None = None
    data_root: Path | None = None
    env: Mapping[str, str] | None = None


@dataclass(frozen=True)
class ProviderSetupPreview:
    config_path: Path
    agent_id: str
    preset_id: str
    display_label: str
    runtime_adapter: str
    api_format_label: str
    model: str
    model_source: str
    base_url: str
    credential: str
    shared_adapter_isolated: bool


@dataclass(frozen=True)
class ProviderSetupResult:
    config: OpenMinionConfig
    config_path: Path
    preset: ProviderSetupPreset
    model_choice: ModelChoiceResult
    preview: ProviderSetupPreview
    changed_sections: tuple[str, ...]


def default_storage_path(data_root: Path) -> str:
    return str((data_root / "state" / "openminion.db").resolve(strict=False))


def resolve_setup_credential(
    *,
    preset: ProviderSetupPreset,
    env: Mapping[str, str] | None,
    stored_api_key: str = "",
    allow_local_api_key: bool = False,
) -> CredentialResolution:
    env_var = preset.credential_env
    if not env_var:
        return CredentialResolution(env_var="", source="not_required")
    env_snapshot = resolve_environment_config().snapshot() if env is None else env
    env_value = (env_snapshot.get(env_var) or "").strip()
    if env_value:
        return CredentialResolution(env_var=env_var, source="env")
    local_value = stored_api_key.strip()
    if local_value and allow_local_api_key:
        return CredentialResolution(
            env_var=env_var,
            source="local_config",
            local_value=local_value,
        )
    if local_value:
        raise ProviderSetupError(
            "Refusing to store a local API key without explicit local-config consent."
        )
    if preset.requires_credential:
        raise ProviderSetupError(
            f"Missing credential for {preset.display_label}. Export {env_var} or "
            "confirm local config storage interactively."
        )
    return CredentialResolution(env_var=env_var, source="not_required")


def build_provider_setup(
    request: ProviderSetupRequest,
    *,
    existing_config: OpenMinionConfig | None = None,
) -> ProviderSetupResult:
    preset = get_setup_preset(request.preset_id)
    config_path = resolve_config_path(request.config_path, home_root=request.home_root)
    data_root = Path(request.data_root or (Path.home() / ".openminion")).expanduser()
    config_exists = existing_config is not None or config_path.exists()
    base_config = _copy_config(
        existing_config if existing_config is not None else _load_existing(config_path)
    )
    agent_id = _normalize_agent_id(request.agent_id)
    credential = resolve_setup_credential(
        preset=preset,
        env=request.env,
        stored_api_key=request.stored_api_key,
        allow_local_api_key=request.allow_local_api_key,
    )
    if preset.discovery_posture == "manual" and not request.model.strip():
        raise ProviderSetupError(
            f"{preset.display_label} requires an explicit model id."
        )
    configured_model = _configured_model(
        base_config,
        preset=preset,
        agent_id=agent_id,
    )
    model_choice = resolve_model_choice(
        preset=preset,
        configured_model=configured_model if config_exists else "",
        manual_model=request.model,
    )
    model = model_choice.selected_model
    if not model:
        raise ProviderSetupError(f"No model selected for {preset.display_label}.")
    base_url = _resolve_base_url(preset=preset, base_url=request.base_url)
    provider_identity = resolve_provider_identity_translation(
        preset.runtime_adapter,
        model=model,
        base_url=base_url,
    )
    config, shared_isolated, changed_sections = _apply_setup_selection(
        base_config,
        preset=preset,
        agent_id=agent_id,
        model=model,
        base_url=base_url,
        credential=credential,
        provider_identity=provider_identity,
        data_root=data_root,
        config_exists=config_exists,
    )
    preview = ProviderSetupPreview(
        config_path=config_path,
        agent_id=agent_id,
        preset_id=preset.preset_id,
        display_label=preset.display_label,
        runtime_adapter=preset.runtime_adapter,
        api_format_label=preset.api_format_label,
        model=model,
        model_source=model_choice.source,
        base_url=base_url,
        credential=_credential_preview(credential),
        shared_adapter_isolated=shared_isolated,
    )
    return ProviderSetupResult(
        config=config,
        config_path=config_path,
        preset=preset,
        model_choice=model_choice,
        preview=preview,
        changed_sections=tuple(changed_sections),
    )


def save_provider_setup(result: ProviderSetupResult) -> Path:
    return atomic_save_setup_config(result.config, result.config_path)


def atomic_save_setup_config(config: OpenMinionConfig, path: Path) -> Path:
    target = Path(path).expanduser().resolve(strict=False)
    payload = json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n"
    parent_exists = target.parent.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not parent_exists:
        _enforce_owner_only_dir(target.parent)
    previous_mode = _existing_mode(target)
    with NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
        try:
            _chmod_owner_only_file(tmp_path)
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        except OSError:
            _remove_temp(tmp_path)
            raise
    try:
        _parse_config_file(tmp_path)
        _apply_final_mode(tmp_path, previous_mode)
        os.replace(tmp_path, target)
    except (OSError, TypeError, ValueError):
        _remove_temp(tmp_path)
        raise
    return target


def redacted_config_payload(config: OpenMinionConfig) -> dict[str, Any]:
    return _redact_mapping(config.to_dict())


def _redact_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        normalized_key = str(key).lower()
        if _looks_secret_key(normalized_key) and str(value or "").strip():
            redacted[key] = "<redacted>"
        elif isinstance(value, dict):
            redacted[key] = _redact_mapping(value)
        elif isinstance(value, list):
            redacted[key] = [
                _redact_mapping(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            redacted[key] = value
    return redacted


def _looks_secret_key(key: str) -> bool:
    if key.endswith("_env"):
        return False
    return key in {"api_key", "token", "secret"} or key.endswith(
        ("_api_key", "_token", "_secret")
    )


def _load_existing(config_path: Path) -> OpenMinionConfig:
    if not config_path.exists():
        return OpenMinionConfig()
    return _parse_config_file(config_path)


def _parse_config_file(path: Path) -> OpenMinionConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProviderSetupError(f"Config at {path} must be a JSON object.")
    return OpenMinionConfig.from_dict(payload)


def _copy_config(config: OpenMinionConfig) -> OpenMinionConfig:
    return OpenMinionConfig.from_dict(config.to_dict())


def _normalize_agent_id(agent_id: str) -> str:
    normalized = agent_id.strip() or "openminion"
    if any(char.isspace() for char in normalized):
        raise ProviderSetupError("Agent id must not contain whitespace.")
    return normalized


def _resolve_base_url(*, preset: ProviderSetupPreset, base_url: str) -> str:
    value = base_url.strip() or preset.default_base_url
    if preset.requires_base_url and not value:
        raise ProviderSetupError(f"{preset.display_label} requires --base-url.")
    if value:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProviderSetupError("Base URL must be an HTTP(S) URL with a hostname.")
    return value


def _configured_model(
    config: OpenMinionConfig,
    *,
    preset: ProviderSetupPreset,
    agent_id: str,
) -> str:
    provider_cfg = getattr(config.providers, preset.runtime_adapter, None)
    if provider_cfg is None:
        return ""

    model = str(getattr(provider_cfg, "model", "") or "").strip()
    base_url = str(getattr(provider_cfg, "base_url", "") or "").strip()
    provider_identity = dict(getattr(provider_cfg, "provider_identity", {}) or {})
    profile = config.agents.get(agent_id)
    if profile is not None:
        if _canonical_provider_name(profile.provider) != _canonical_provider_name(
            preset.runtime_adapter
        ):
            return ""
        overrides = dict(profile.provider_config_overrides or {})
        model = str(overrides.get("model", model) or "").strip()
        base_url = str(overrides.get("base_url", base_url) or "").strip()
        provider_identity = dict(
            overrides.get("provider_identity", provider_identity) or {}
        )

    if not model or preset.requires_base_url:
        return ""
    if preset.runtime_adapter != "openai":
        return model

    configured_identity = provider_identity or resolve_provider_identity_translation(
        "openai",
        model=model,
        base_url=base_url,
    )
    expected_identity = resolve_provider_identity_translation(
        "openai",
        model=preset.recommended_models[0],
        base_url=preset.default_base_url,
    )
    configured_vendor = configured_identity.get("service_vendor", "")
    expected_vendor = expected_identity.get("service_vendor", "")
    if configured_vendor != expected_vendor:
        return ""
    if expected_vendor == "openai" and not _same_endpoint(
        base_url, preset.default_base_url
    ):
        return ""
    return model


def _same_endpoint(left: str, right: str) -> bool:
    return left.rstrip("/").lower() == right.rstrip("/").lower()


def _canonical_provider_name(provider_name: str) -> str:
    normalized = provider_name.strip().lower()
    return _PROVIDER_ALIASES.get(normalized, normalized)


def _apply_setup_selection(
    config: OpenMinionConfig,
    *,
    preset: ProviderSetupPreset,
    agent_id: str,
    model: str,
    base_url: str,
    credential: CredentialResolution,
    provider_identity: Mapping[str, str],
    data_root: Path,
    config_exists: bool,
) -> tuple[OpenMinionConfig, bool, list[str]]:
    changed = ["agents", "default_agent"]
    adapter = preset.runtime_adapter
    canonical_adapter = _canonical_provider_name(adapter)
    config.runtime.demo_mode = False
    if not config_exists or not str(config.storage.path or "").strip():
        config.storage.path = default_storage_path(data_root)
        changed.append("storage")
    shared_adapter = any(
        existing_id != agent_id
        and _canonical_provider_name(profile.provider) == canonical_adapter
        for existing_id, profile in config.agents.items()
    )
    provider_patch = _provider_patch(
        model=model,
        base_url=base_url,
        credential=credential,
        provider_identity=provider_identity,
    )
    profile = config.agents.get(agent_id) or AgentProfileConfig(name=agent_id)
    profile.name = profile.name or agent_id
    profile.provider = adapter
    profile.default_channel = profile.default_channel or "console"
    unmanaged_overrides = _unmanaged_provider_overrides(
        profile.provider_config_overrides
    )
    if shared_adapter:
        profile.provider_config_overrides = {
            **unmanaged_overrides,
            **provider_patch,
        }
    else:
        profile.provider_config_overrides = unmanaged_overrides
        provider_cfg = getattr(config.providers, adapter, None)
        if provider_cfg is None:
            raise ProviderSetupError(
                f"Runtime adapter {adapter!r} is not supported by OpenMinionConfig."
            )
        for key, value in provider_patch.items():
            setattr(provider_cfg, key, value)
        changed.append(f"providers.{adapter}")
    config.agents[agent_id] = profile
    config.default_agent = agent_id
    build_runtime_config(config, agent_id=agent_id)
    return config, shared_adapter, changed


def _provider_patch(
    *,
    model: str,
    base_url: str,
    credential: CredentialResolution,
    provider_identity: Mapping[str, str],
) -> dict[str, Any]:
    patch: dict[str, Any] = {"model": model}
    if base_url:
        patch["base_url"] = base_url
    if provider_identity:
        patch["provider_identity"] = dict(provider_identity)
    if credential.env_var:
        patch["api_key_env"] = credential.env_var
        patch["api_key"] = ""
    if credential.local_value:
        patch["api_key"] = credential.local_value
    return patch


def _unmanaged_provider_overrides(
    overrides: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(overrides or {}).items()
        if key not in _MANAGED_PROVIDER_OVERRIDE_KEYS
    }


def _credential_preview(credential: CredentialResolution) -> str:
    if credential.source == "env":
        return f"environment variable {credential.env_var}"
    if credential.source == "local_config":
        return "local config <redacted>"
    if credential.source == "not_required":
        return "not required"
    return "missing"


def _existing_mode(path: Path) -> int | None:
    try:
        return path.stat().st_mode & 0o777
    except FileNotFoundError:
        return None


def _enforce_owner_only_dir(path: Path) -> None:
    if os.name != "posix":
        return
    os.chmod(path, 0o700)


def _chmod_owner_only_file(path: Path) -> None:
    if os.name != "posix":
        return
    os.chmod(path, 0o600)


def _apply_final_mode(path: Path, previous_mode: int | None) -> None:
    if os.name != "posix":
        return
    final_mode = _final_owner_only_file_mode(previous_mode)
    os.chmod(path, final_mode)
    observed_mode = stat.S_IMODE(path.stat().st_mode)
    if observed_mode != final_mode:
        raise ProviderSetupError(
            f"Config file permissions must be {final_mode:o}; observed {observed_mode:o}."
        )


def _final_owner_only_file_mode(previous_mode: int | None) -> int:
    if previous_mode is not None and previous_mode & 0o077 == 0:
        return previous_mode
    return 0o600


def _remove_temp(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


__all__ = [
    "CredentialResolution",
    "ProviderSetupError",
    "ProviderSetupPreview",
    "ProviderSetupRequest",
    "ProviderSetupResult",
    "atomic_save_setup_config",
    "build_provider_setup",
    "default_storage_path",
    "redacted_config_payload",
    "resolve_setup_credential",
    "save_provider_setup",
]
