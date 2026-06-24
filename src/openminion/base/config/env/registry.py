"""Canonical OpenMinion environment variable registry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnvVarSpec:
    name: str
    value_type: str
    default: str
    owner: str
    description: str
    deprecated: bool = False
    replacement: str = ""
    deprecation_guidance: str = ""


ENV_VAR_SPECS: tuple[EnvVarSpec, ...] = (
    EnvVarSpec(
        name="OPENMINION_HOME",
        value_type="path",
        default="",
        owner="base/config",
        description="Base root used to anchor generated state and default data paths.",
    ),
    EnvVarSpec(
        name="OPENMINION_DATA_ROOT",
        value_type="path",
        default="",
        owner="base/config",
        description="Overrides canonical data root (otherwise resolved under OPENMINION_HOME).",
    ),
    EnvVarSpec(
        name="OPENMINION_DATA_ROOT_ENFORCEMENT",
        value_type="enum(hard|soft|warn)",
        default="hard",
        owner="base/config",
        description="Controls whether module paths must stay under data_root.",
    ),
    EnvVarSpec(
        name="OPENMINION_GENERATED_ROOT",
        value_type="path",
        default="",
        owner="base/generated_paths",
        description="Overrides generated artifact root.",
    ),
    EnvVarSpec(
        name="OPENMINION_CONFIG_ROOT",
        value_type="path",
        default="",
        owner="base/config/io",
        description="Overrides config directory root for config.json lookup.",
        deprecated=True,
        replacement="OPENMINION_HOME",
        deprecation_guidance=(
            "Use OPENMINION_HOME with OPENMINION_DATA_ROOT for path control."
        ),
    ),
    EnvVarSpec(
        name="OPENMINION_CONFIG_PATH",
        value_type="path",
        default="",
        owner="base/config",
        description="Explicit config file path override.",
    ),
    EnvVarSpec(
        name="OPENMINION_MODULE_STANDALONE",
        value_type="bool",
        default="false",
        owner="module CLIs",
        description="If true, module CLIs use standalone defaults instead of centralized data root.",
    ),
    EnvVarSpec(
        name="OPENMINION_LOG_LEVEL",
        value_type="string",
        default="",
        owner="base/logging",
        description="Overrides logger level selection.",
    ),
    EnvVarSpec(
        name="OPENMINION_COLOR",
        value_type="bool",
        default="auto",
        owner="cli/logging",
        description="Controls ANSI color rendering in CLI and logs.",
    ),
    EnvVarSpec(
        name="OPENMINION_LOG_COLOR",
        value_type="bool",
        default="",
        owner="legacy",
        description="Legacy log color toggle.",
        deprecated=True,
        replacement="OPENMINION_COLOR",
        deprecation_guidance="Use OPENMINION_COLOR instead.",
    ),
    EnvVarSpec(
        name="OPENMINION_TRACE_REQUESTS",
        value_type="bool",
        default="false",
        owner="llm/telemetry",
        description="Enable request/response trace capture to disk.",
    ),
    EnvVarSpec(
        name="OPENMINION_TRACE_REQUESTS_DIR",
        value_type="path",
        default="",
        owner="llm/telemetry",
        description="Explicit trace output directory when tracing is enabled.",
    ),
    EnvVarSpec(
        name="OPENMINION_LLM_DEBUG",
        value_type="bool",
        default="false",
        owner="llm/providers",
        description="Enable extra provider debug output.",
    ),
    EnvVarSpec(
        name="OPENMINION_LLM_DEBUG_PROVIDER",
        value_type="string",
        default="",
        owner="llm/providers",
        description="Provider-specific debug filter.",
    ),
    EnvVarSpec(
        name="OPENMINION_LLM_DEBUG_DIR",
        value_type="path",
        default="",
        owner="llm/providers",
        description="Directory for LLM debug artifacts.",
    ),
    EnvVarSpec(
        name="OPENMINION_LLM_DEBUG_MAX_CHARS",
        value_type="int",
        default="0",
        owner="llm/providers",
        description="Max debug chars retained per payload.",
    ),
    EnvVarSpec(
        name="OPENMINION_STRICT_PROVIDER_RESPONSE_CONTRACTS",
        value_type="bool",
        default="false",
        owner="llm/providers",
        description="Fail provider responses that violate normalized contracts.",
    ),
    EnvVarSpec(
        name="OPENMINION_DISABLE_LLMCTL_BRIDGE",
        value_type="bool",
        default="false",
        owner="llm/providers",
        description="Disable llmctl bridge and use direct provider path.",
    ),
    EnvVarSpec(
        name="OPENMINION_DISABLE_SECURITY_POLICY",
        value_type="bool",
        default="false",
        owner="services/runtime",
        description="Disable security policy engine bootstrap (testing/dev only).",
    ),
    EnvVarSpec(
        name="OPENMINION_TURN_TIMEOUT_SECONDS",
        value_type="int",
        default="0",
        owner="services/gateway",
        description="Global turn timeout override in seconds.",
    ),
    EnvVarSpec(
        name="OPENMINION_AGENT_ID",
        value_type="string",
        default="openminion",
        owner="tools/location,time,exec",
        description="Default agent identity token when not provided by runtime context.",
    ),
    EnvVarSpec(
        name="OPENMINION_IDENTITY_DB",
        value_type="path",
        default="${OPENMINION_IDENTITY_ROOT}/identity.db",
        owner="base/config/runtime",
        description=(
            "Identity sqlite path override for profile-backed defaults. "
            "Default derives from OPENMINION_IDENTITY_ROOT/identity.db."
        ),
    ),
    EnvVarSpec(
        name="OPENMINION_IDENTITY_ROOT",
        value_type="path",
        default="${OPENMINION_DATA_ROOT}/identity",
        owner="base/config/runtime",
        description=(
            "Identity root override for startup YAML sync and bundle discovery. "
            "Each direct subdirectory is an agent and profile.yaml is the YAML source."
        ),
    ),
    EnvVarSpec(
        name="OPENMINION_TIMEZONE",
        value_type="IANA timezone",
        default="UTC",
        owner="tools/time",
        description="Fallback timezone when identity/session metadata does not provide one.",
    ),
    EnvVarSpec(
        name="OPENMINION_WEB_SEARCH_PROVIDER",
        value_type="enum(auto|brave|tavily|serpapi|firecrawl|serper|tinyfish)",
        default="auto",
        owner="tools/search",
        description="Optional provider forcing for web_search routing.",
    ),
    EnvVarSpec(
        name="OPENMINION_TOOL_EXEC_ENABLE_HOST_EXEC",
        value_type="bool",
        default="false",
        owner="tools/exec",
        description="Allow host mode for exec tool when enabled.",
    ),
    EnvVarSpec(
        name="OPENMINION_TOOL_EXEC_ALLOWLIST_PATHS",
        value_type="csv paths",
        default="",
        owner="tools/exec",
        description="Additional absolute executable paths allowed for host exec mode.",
    ),
    EnvVarSpec(
        name="OPENMINION_TOOL_EXEC_SAFE_BINS",
        value_type="csv",
        default="cat,head,tail,grep,sed,awk,tr,cut,sort,uniq,wc",
        owner="tools/exec",
        description="Safe executable names allowed in trusted dirs for host exec mode.",
    ),
    EnvVarSpec(
        name="OPENMINION_TOOL_EXEC_SAFE_BIN_TRUSTED_DIRS",
        value_type="csv paths",
        default="/bin,/usr/bin,/usr/local/bin",
        owner="tools/exec",
        description="Trusted directories for safe executable names.",
    ),
    EnvVarSpec(
        name="OPENAI_API_KEY",
        value_type="secret",
        default="",
        owner="llm/providers",
        description="API key fallback for OpenAI provider.",
    ),
    EnvVarSpec(
        name="ANTHROPIC_API_KEY",
        value_type="secret",
        default="",
        owner="llm/providers",
        description="API key fallback for Anthropic provider.",
    ),
    EnvVarSpec(
        name="OPENROUTER_API_KEY",
        value_type="secret",
        default="",
        owner="llm/providers",
        description="API key fallback for OpenRouter provider.",
    ),
    EnvVarSpec(
        name="CEREBRAS_API_KEY",
        value_type="secret",
        default="",
        owner="llm/providers",
        description="API key fallback for Cerebras provider.",
    ),
    EnvVarSpec(
        name="GROQ_API_KEY",
        value_type="secret",
        default="",
        owner="llm/providers",
        description="API key fallback for Groq provider.",
    ),
    EnvVarSpec(
        name="OLLAMA_API_KEY",
        value_type="secret",
        default="",
        owner="llm/providers",
        description="API key fallback for Ollama provider.",
    ),
    EnvVarSpec(
        name="CORTENSOR_API_KEY",
        value_type="secret",
        default="",
        owner="llm/providers",
        description="API key fallback for Cortensor provider.",
    ),
    EnvVarSpec(
        name="TAVILY_API_KEY",
        value_type="secret",
        default="",
        owner="tools/search/providers/tavily",
        description="API key for Tavily web search.",
    ),
    EnvVarSpec(
        name="TAVILY_API_URL",
        value_type="url",
        default="https://api.tavily.com/search",
        owner="tools/search/providers/tavily",
        description="Optional Tavily API URL override.",
    ),
    EnvVarSpec(
        name="BRAVE_API_KEY",
        value_type="secret",
        default="",
        owner="tools/search/providers/brave",
        description="API key for Brave web search.",
    ),
    EnvVarSpec(
        name="SERPAPI_API_KEY",
        value_type="secret",
        default="",
        owner="tools/search/providers/serpapi",
        description="API key for SerpApi web search.",
    ),
    EnvVarSpec(
        name="SERPAPI_API_URL",
        value_type="url",
        default="https://serpapi.com/search",
        owner="tools/search/providers/serpapi",
        description="Optional SerpApi API URL override.",
    ),
    EnvVarSpec(
        name="SERPAPI_TIMEOUT_SECONDS",
        value_type="float",
        default="20.0",
        owner="tools/search/providers/serpapi",
        description="Optional SerpApi timeout override in seconds.",
    ),
    EnvVarSpec(
        name="SERPER_API_KEY",
        value_type="secret",
        default="",
        owner="tools/search/providers/serper",
        description="API key for Serper web search.",
    ),
    EnvVarSpec(
        name="SERPER_API_URL",
        value_type="url",
        default="https://google.serper.dev/search",
        owner="tools/search/providers/serper",
        description="Optional Serper API URL override.",
    ),
    EnvVarSpec(
        name="SERPER_TIMEOUT_SECONDS",
        value_type="float",
        default="20.0",
        owner="tools/search/providers/serper",
        description="Optional Serper timeout override in seconds.",
    ),
    EnvVarSpec(
        name="FIRECRAWL_API_KEY",
        value_type="secret",
        default="",
        owner="tools/firecrawl providers",
        description="API key for Firecrawl-backed search/fetch providers.",
    ),
    EnvVarSpec(
        name="FIRECRAWL_API_URL",
        value_type="url",
        default="https://api.firecrawl.dev",
        owner="tools/firecrawl providers",
        description="Optional Firecrawl API URL override shared by Firecrawl providers.",
    ),
    EnvVarSpec(
        name="FIRECRAWL_TIMEOUT_SECONDS",
        value_type="float",
        default="20.0",
        owner="tools/firecrawl providers",
        description="Optional timeout override in seconds shared by Firecrawl providers.",
    ),
    EnvVarSpec(
        name="TINYFISH_API_KEY",
        value_type="secret",
        default="",
        owner="tools/tinyfish providers",
        description="API key shared by TinyFish-backed search and fetch providers.",
    ),
    EnvVarSpec(
        name="TINYFISH_SEARCH_API_URL",
        value_type="url",
        default="https://api.search.tinyfish.ai",
        owner="tools/search/providers/tinyfish",
        description="Optional TinyFish Search API URL override.",
    ),
    EnvVarSpec(
        name="TINYFISH_SEARCH_TIMEOUT_SECONDS",
        value_type="float",
        default="20.0",
        owner="tools/search/providers/tinyfish",
        description="Optional TinyFish Search timeout override in seconds.",
    ),
    EnvVarSpec(
        name="TINYFISH_FETCH_API_URL",
        value_type="url",
        default="https://api.fetch.tinyfish.ai",
        owner="tools/fetch/providers/tinyfish",
        description="Optional TinyFish Fetch API URL override.",
    ),
    EnvVarSpec(
        name="TINYFISH_FETCH_TIMEOUT_SECONDS",
        value_type="float",
        default="150.0",
        owner="tools/fetch/providers/tinyfish",
        description="Optional TinyFish Fetch timeout override in seconds.",
    ),
    EnvVarSpec(
        name="WEATHERAPI_API_KEY",
        value_type="secret",
        default="",
        owner="tools/weather/providers/weatherapi",
        description="API key for WeatherAPI.com weather provider.",
    ),
    EnvVarSpec(
        name="WEATHERAPI_API_URL",
        value_type="url",
        default="https://api.weatherapi.com/v1",
        owner="tools/weather/providers/weatherapi",
        description="Optional WeatherAPI base URL override.",
    ),
    EnvVarSpec(
        name="WEATHERAPI_TIMEOUT_SECONDS",
        value_type="float",
        default="20.0",
        owner="tools/weather/providers/weatherapi",
        description="Optional WeatherAPI timeout override in seconds.",
    ),
)


def get_env_var_specs() -> tuple[EnvVarSpec, ...]:
    return ENV_VAR_SPECS


def iter_deprecated_env_specs() -> tuple[EnvVarSpec, ...]:
    return tuple(spec for spec in ENV_VAR_SPECS if spec.deprecated)


__all__ = [
    "EnvVarSpec",
    "ENV_VAR_SPECS",
    "get_env_var_specs",
    "iter_deprecated_env_specs",
]
