"""Setup-facing provider catalog and model provenance helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

ModelChoiceSource = Literal["live", "configured", "recommended", "manual"]
ModelDiscoveryPosture = Literal[
    "live_optional", "configured", "recommended_only", "manual"
]


class SetupCatalogError(ValueError):
    """Raised when setup catalog metadata is invalid or unsupported."""


@dataclass(frozen=True)
class ProviderSetupPreset:
    preset_id: str
    display_label: str
    runtime_adapter: str
    api_format_label: str
    api_format_id: str
    default_base_url: str
    credential_env: str
    recommended_models: tuple[str, ...]
    discovery_posture: ModelDiscoveryPosture
    is_local: bool = False
    setup_help_url: str = ""
    requires_base_url: bool = False

    @property
    def requires_credential(self) -> bool:
        return bool(self.credential_env) and not self.is_local


@dataclass(frozen=True)
class ModelChoiceResult:
    preset_id: str
    models: tuple[str, ...]
    source: ModelChoiceSource
    warning: str = ""
    endpoint_verified: bool = False
    discovery_duration_seconds: float = 0.0

    @property
    def selected_model(self) -> str:
        return self.models[0] if self.models else ""


_PRESETS: tuple[ProviderSetupPreset, ...] = (
    ProviderSetupPreset(
        preset_id="openai",
        display_label="OpenAI",
        runtime_adapter="openai",
        api_format_label="OpenAI-compatible",
        api_format_id="openai-compatible",
        default_base_url="https://api.openai.com/v1",
        credential_env="OPENAI_API_KEY",
        recommended_models=("gpt-4.1-mini",),
        discovery_posture="live_optional",
        setup_help_url="https://platform.openai.com/api-keys",
    ),
    ProviderSetupPreset(
        preset_id="anthropic",
        display_label="Anthropic",
        runtime_adapter="anthropic",
        api_format_label="Anthropic Messages",
        api_format_id="anthropic-messages",
        default_base_url="https://api.anthropic.com/v1",
        credential_env="ANTHROPIC_API_KEY",
        recommended_models=("claude-sonnet-5",),
        discovery_posture="recommended_only",
        setup_help_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderSetupPreset(
        preset_id="openrouter",
        display_label="OpenRouter",
        runtime_adapter="openrouter",
        api_format_label="OpenAI-compatible via OpenRouter",
        api_format_id="openai-compatible",
        default_base_url="https://openrouter.ai/api/v1",
        credential_env="OPENROUTER_API_KEY",
        recommended_models=("openai/gpt-4.1-mini",),
        discovery_posture="live_optional",
        setup_help_url="https://openrouter.ai/settings/keys",
    ),
    ProviderSetupPreset(
        preset_id="ollama",
        display_label="Ollama",
        runtime_adapter="ollama",
        api_format_label="Ollama local chat",
        api_format_id="ollama-local-chat",
        default_base_url="http://127.0.0.1:11434",
        credential_env="",
        recommended_models=("llama3.1",),
        discovery_posture="live_optional",
        is_local=True,
    ),
    ProviderSetupPreset(
        preset_id="cerebras",
        display_label="Cerebras",
        runtime_adapter="cerebras",
        api_format_label="OpenAI-compatible",
        api_format_id="openai-compatible",
        default_base_url="https://api.cerebras.ai/v1",
        credential_env="CEREBRAS_API_KEY",
        recommended_models=("gpt-oss-120b",),
        discovery_posture="live_optional",
        setup_help_url="https://cloud.cerebras.ai/platform/",
    ),
    ProviderSetupPreset(
        preset_id="groq",
        display_label="Groq",
        runtime_adapter="groq",
        api_format_label="OpenAI-compatible",
        api_format_id="openai-compatible",
        default_base_url="https://api.groq.com/openai/v1",
        credential_env="GROQ_API_KEY",
        recommended_models=("llama-3.3-70b-versatile",),
        discovery_posture="live_optional",
        setup_help_url="https://console.groq.com/keys",
    ),
    ProviderSetupPreset(
        preset_id="cortensor",
        display_label="Cortensor",
        runtime_adapter="cortensor",
        api_format_label="Cortensor completions",
        api_format_id="cortensor-completions",
        default_base_url="http://127.0.0.1:8080/api/v2/completions",
        credential_env="CORTENSOR_API_KEY",
        recommended_models=("gpt-oss-20b",),
        discovery_posture="configured",
    ),
    ProviderSetupPreset(
        preset_id="minimax",
        display_label="MiniMax",
        runtime_adapter="openai",
        api_format_label="OpenAI-compatible",
        api_format_id="openai-compatible",
        default_base_url="https://api.minimax.io/v1",
        credential_env="MINIMAX_API_KEY",
        recommended_models=("MiniMax-M2.7", "MiniMax-M2.7-highspeed"),
        discovery_posture="live_optional",
        setup_help_url="https://platform.minimax.io/docs/api-reference/text-openai-api",
    ),
    ProviderSetupPreset(
        preset_id="kimi",
        display_label="Kimi / Moonshot AI",
        runtime_adapter="openai",
        api_format_label="OpenAI-compatible",
        api_format_id="openai-compatible",
        default_base_url="https://api.moonshot.ai/v1",
        credential_env="MOONSHOT_API_KEY",
        recommended_models=("kimi-k2.6",),
        discovery_posture="recommended_only",
        setup_help_url="https://platform.kimi.ai/docs/overview",
    ),
    ProviderSetupPreset(
        preset_id="zai",
        display_label="Z.ai",
        runtime_adapter="openai",
        api_format_label="OpenAI-compatible",
        api_format_id="openai-compatible",
        default_base_url="https://api.z.ai/api/paas/v4/",
        credential_env="ZAI_API_KEY",
        recommended_models=("glm-5.2",),
        discovery_posture="recommended_only",
        setup_help_url="https://docs.z.ai/guides/develop/openai/python",
    ),
    ProviderSetupPreset(
        preset_id="zai-coding",
        display_label="Z.ai Coding",
        runtime_adapter="openai",
        api_format_label="OpenAI-compatible coding endpoint",
        api_format_id="openai-compatible",
        default_base_url="https://api.z.ai/api/coding/paas/v4",
        credential_env="ZAI_API_KEY",
        recommended_models=("glm-5.2",),
        discovery_posture="recommended_only",
        setup_help_url="https://docs.z.ai/devpack/tool/others",
    ),
    ProviderSetupPreset(
        preset_id="deepseek",
        display_label="DeepSeek",
        runtime_adapter="openai",
        api_format_label="OpenAI-compatible",
        api_format_id="openai-compatible",
        default_base_url="https://api.deepseek.com",
        credential_env="DEEPSEEK_API_KEY",
        recommended_models=("deepseek-v4-flash", "deepseek-v4-pro"),
        discovery_posture="recommended_only",
        setup_help_url="https://api-docs.deepseek.com/",
    ),
    ProviderSetupPreset(
        preset_id="qwen-dashscope",
        display_label="Qwen via DashScope",
        runtime_adapter="openai",
        api_format_label="OpenAI-compatible via DashScope",
        api_format_id="openai-compatible",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        credential_env="DASHSCOPE_API_KEY",
        recommended_models=("qwen3.7-plus",),
        discovery_posture="recommended_only",
        setup_help_url=(
            "https://www.alibabacloud.com/help/en/model-studio/"
            "compatibility-of-openai-with-dashscope"
        ),
    ),
    ProviderSetupPreset(
        preset_id="gemini",
        display_label="Gemini",
        runtime_adapter="openai",
        api_format_label="OpenAI-compatible",
        api_format_id="openai-compatible",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        credential_env="GEMINI_API_KEY",
        recommended_models=("gemini-3.6-flash",),
        discovery_posture="live_optional",
        setup_help_url="https://ai.google.dev/gemini-api/docs/openai",
    ),
    ProviderSetupPreset(
        preset_id="xai",
        display_label="xAI",
        runtime_adapter="openai",
        api_format_label="OpenAI-compatible",
        api_format_id="openai-compatible",
        default_base_url="https://api.x.ai/v1",
        credential_env="XAI_API_KEY",
        recommended_models=("grok-4.5",),
        discovery_posture="recommended_only",
        setup_help_url="https://docs.x.ai/developers/rest-api-reference",
    ),
    ProviderSetupPreset(
        preset_id="mistral",
        display_label="Mistral AI",
        runtime_adapter="openai",
        api_format_label="OpenAI-compatible",
        api_format_id="openai-compatible",
        default_base_url="https://api.mistral.ai/v1",
        credential_env="MISTRAL_API_KEY",
        recommended_models=("mistral-large-latest",),
        discovery_posture="recommended_only",
        setup_help_url="https://docs.mistral.ai/resources/migration-guides",
    ),
    ProviderSetupPreset(
        preset_id="together",
        display_label="Together AI",
        runtime_adapter="openai",
        api_format_label="OpenAI-compatible",
        api_format_id="openai-compatible",
        default_base_url="https://api.together.ai/v1",
        credential_env="TOGETHER_API_KEY",
        recommended_models=("MiniMaxAI/MiniMax-M2.7",),
        discovery_posture="recommended_only",
        setup_help_url="https://docs.together.ai/docs/inference/openai-compatibility",
    ),
    ProviderSetupPreset(
        preset_id="custom-openai-compatible",
        display_label="Custom OpenAI-compatible endpoint",
        runtime_adapter="openai",
        api_format_label="OpenAI-compatible",
        api_format_id="openai-compatible",
        default_base_url="",
        credential_env="OPENAI_COMPATIBLE_API_KEY",
        recommended_models=("model-id",),
        discovery_posture="manual",
        requires_base_url=True,
    ),
    ProviderSetupPreset(
        preset_id="custom-anthropic-compatible",
        display_label="Custom Anthropic-compatible endpoint",
        runtime_adapter="anthropic",
        api_format_label="Anthropic-compatible",
        api_format_id="anthropic-compatible",
        default_base_url="",
        credential_env="ANTHROPIC_COMPATIBLE_API_KEY",
        recommended_models=("model-id",),
        discovery_posture="manual",
        requires_base_url=True,
    ),
)

_PRESET_BY_ID = {preset.preset_id: preset for preset in _PRESETS}
_FIRST_SCREEN_IDS = ("openai", "anthropic", "openrouter", "minimax")


def list_setup_presets() -> tuple[ProviderSetupPreset, ...]:
    _validate_catalog()
    return _PRESETS


def first_screen_presets() -> tuple[ProviderSetupPreset, ...]:
    return tuple(get_setup_preset(preset_id) for preset_id in _FIRST_SCREEN_IDS)


def more_screen_presets() -> tuple[ProviderSetupPreset, ...]:
    first = set(_FIRST_SCREEN_IDS)
    return tuple(
        preset
        for preset in list_setup_presets()
        if preset.preset_id not in first and not preset.is_local
    )


def get_setup_preset(preset_id: str) -> ProviderSetupPreset:
    normalized = str(preset_id or "").strip().lower()
    try:
        return _PRESET_BY_ID[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(_PRESET_BY_ID))
        raise SetupCatalogError(
            f"Unsupported provider preset {preset_id!r}. Supported presets: {supported}."
        ) from exc


def resolve_model_choice(
    *,
    preset: ProviderSetupPreset,
    configured_model: str = "",
    manual_model: str = "",
    list_models: Callable[[ProviderSetupPreset], Iterable[str]] | None = None,
) -> ModelChoiceResult:
    manual = str(manual_model or "").strip()
    if manual:
        return ModelChoiceResult(
            preset_id=preset.preset_id,
            models=(manual,),
            source=("recommended" if manual in preset.recommended_models else "manual"),
        )

    started = perf_counter()
    if list_models is not None and preset.discovery_posture == "live_optional":
        try:
            live_models = tuple(
                model
                for model in (str(item or "").strip() for item in list_models(preset))
                if model
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            live_models = ()
            warning = f"Model discovery unavailable: {exc}"
        else:
            warning = ""
        duration = max(0.0, perf_counter() - started)
        if live_models:
            return ModelChoiceResult(
                preset_id=preset.preset_id,
                models=live_models,
                source="live",
                endpoint_verified=True,
                discovery_duration_seconds=duration,
            )
    else:
        duration = 0.0
        warning = ""

    configured = str(configured_model or "").strip()
    if configured:
        return ModelChoiceResult(
            preset_id=preset.preset_id,
            models=(configured,),
            source="configured",
            warning=warning,
            discovery_duration_seconds=duration,
        )

    return ModelChoiceResult(
        preset_id=preset.preset_id,
        models=tuple(preset.recommended_models),
        source="recommended",
        warning=warning,
        discovery_duration_seconds=duration,
    )


def _validate_catalog() -> None:
    seen: set[str] = set()
    for preset in _PRESETS:
        if not preset.preset_id:
            raise SetupCatalogError(
                "Provider setup catalog contains an empty preset id."
            )
        if preset.preset_id in seen:
            raise SetupCatalogError(
                f"Provider setup catalog contains duplicate preset id {preset.preset_id!r}."
            )
        seen.add(preset.preset_id)
        if not preset.runtime_adapter:
            raise SetupCatalogError(
                f"Provider setup preset {preset.preset_id!r} is missing runtime_adapter."
            )
        if not preset.api_format_id:
            raise SetupCatalogError(
                f"Provider setup preset {preset.preset_id!r} is missing api_format_id."
            )
        if preset.requires_base_url and preset.default_base_url:
            raise SetupCatalogError(
                f"Custom setup preset {preset.preset_id!r} must not hardcode a base URL."
            )
        if not preset.recommended_models:
            raise SetupCatalogError(
                f"Provider setup preset {preset.preset_id!r} needs a fallback model."
            )


__all__ = [
    "ModelChoiceResult",
    "ProviderSetupPreset",
    "SetupCatalogError",
    "first_screen_presets",
    "get_setup_preset",
    "list_setup_presets",
    "more_screen_presets",
    "resolve_model_choice",
]
