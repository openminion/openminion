"""MCP config normalization and policy dataclasses."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlparse

from openminion.base.config.base import ConfigError

_MCP_SERVER_NAME_INVALID_CHARS_RE = re.compile(r"[^a-z0-9]+")
_MCP_TOOL_SEGMENT_INVALID_CHARS_RE = re.compile(r"[^a-z0-9]+")
_VALID_MCP_TOOL_SCOPES = frozenset(
    {"READ_ONLY", "WRITE_SAFE", "POWER_USER", "UI_AUTOMATION"}
)
_VALID_MCP_SAMPLING_MODES = frozenset({"disabled", "deny", "allow"})
_VALID_MCP_APPROVAL_MODES = frozenset({"never", "always", "dangerous", "matching"})
_VALID_MCP_PUBLISH_TRANSPORTS = frozenset({"stdio", "streamable_http"})


def normalize_mcp_server_name(value: object) -> str:
    raw = str(value or "").strip().lower()
    normalized = _MCP_SERVER_NAME_INVALID_CHARS_RE.sub("_", raw)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        raise ConfigError(
            "runtime.mcp_servers[].name must contain at least one letter or digit."
        )
    if not normalized[0].isalpha():
        raise ConfigError(
            "runtime.mcp_servers[].name must start with a letter after normalization."
        )
    return normalized


def normalize_mcp_tool_segment(value: object) -> str:
    raw = str(value or "").strip().lower()
    normalized = _MCP_TOOL_SEGMENT_INVALID_CHARS_RE.sub("_", raw)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        raise ConfigError("MCP tool name must contain at least one letter or digit.")
    return normalized


def normalize_mcp_transport(value: object) -> str:
    token = str(value or "stdio").strip().lower() or "stdio"
    if token not in {"stdio", "streamable_http"}:
        raise ConfigError(
            "runtime.mcp_servers[].transport only supports 'stdio' or "
            "'streamable_http'."
        )
    return token


def normalize_mcp_sampling_mode(value: object) -> str:
    token = str(value or "disabled").strip().lower() or "disabled"
    if token not in _VALID_MCP_SAMPLING_MODES:
        raise ConfigError(
            "runtime.mcp_sampling_mode must be one of 'disabled', 'deny', or 'allow'."
        )
    return token


def _normalize_mcp_command(command: object) -> list[str]:
    if not isinstance(command, list):
        raise ConfigError(
            "runtime.mcp_servers[].command must be a non-empty string array."
        )
    normalized = [str(item).strip() for item in command if str(item).strip()]
    if not normalized:
        raise ConfigError(
            "runtime.mcp_servers[].command must be a non-empty string array."
        )
    return normalized


def _normalize_non_empty_string_map(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        value = str(raw_value or "").strip()
        if not key or not value:
            continue
        normalized[key] = value
    return dict(sorted(normalized.items()))


def _normalize_mcp_url(value: object) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("runtime.mcp_servers[].url must be an absolute http(s) URL.")
    if parsed.fragment:
        raise ConfigError("runtime.mcp_servers[].url must not include a fragment.")
    return url


def _normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _contains_env_interpolation(value: str) -> bool:
    token = str(value or "")
    return "${" in token or "$(" in token or "%{" in token


def _normalize_pattern_list(value: object, *, field_path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{field_path} must be an array of non-empty strings.")
    normalized: list[str] = []
    for index, item in enumerate(value):
        token = str(item or "").strip()
        if not token:
            raise ConfigError(f"{field_path}[{index}] must be a non-empty string.")
        normalized.append(token)
    return normalized


def _normalize_server_list(value: object, *, field_path: str) -> list[str]:
    return [
        normalize_mcp_server_name(item)
        for item in _normalize_pattern_list(value, field_path=field_path)
    ]


@dataclass
class MCPAuthorizationConfig:
    mode: str = "none"
    bearer_token: str = ""
    client_id: str = ""
    client_secret_ref: str = ""
    authorization_server_metadata_url: str = ""
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    registration_endpoint: str = ""
    revocation_endpoint: str = ""
    redirect_uri: str = ""
    scope: str = ""
    access_token: str = ""
    access_token_ref: str = ""
    refresh_token_ref: str = ""

    def __post_init__(self) -> None:
        token = str(self.mode or "none").strip().lower() or "none"
        if token not in {"none", "bearer", "oauth_pkce"}:
            raise ConfigError(
                "runtime.mcp_servers[].authorization.mode only supports "
                "'none', 'bearer', or 'oauth_pkce'."
            )
        self.mode = token
        self.bearer_token = str(self.bearer_token or "").strip()
        self.client_id = str(self.client_id or "").strip()
        self.client_secret_ref = str(self.client_secret_ref or "").strip()
        self.authorization_server_metadata_url = _normalize_mcp_url(
            self.authorization_server_metadata_url
        )
        self.authorization_endpoint = _normalize_mcp_url(self.authorization_endpoint)
        self.token_endpoint = _normalize_mcp_url(self.token_endpoint)
        self.registration_endpoint = _normalize_mcp_url(self.registration_endpoint)
        self.revocation_endpoint = _normalize_mcp_url(self.revocation_endpoint)
        self.redirect_uri = str(self.redirect_uri or "").strip()
        self.scope = str(self.scope or "").strip()
        self.access_token = str(self.access_token or "").strip()
        self.access_token_ref = str(self.access_token_ref or "").strip()
        self.refresh_token_ref = str(self.refresh_token_ref or "").strip()
        if self.mode == "bearer" and not self.bearer_token:
            raise ConfigError(
                "runtime.mcp_servers[].authorization.bearer_token is required "
                "when authorization.mode='bearer'."
            )
        if self.mode == "oauth_pkce":
            if not self.client_id:
                raise ConfigError(
                    "runtime.mcp_servers[].authorization.client_id is required "
                    "when authorization.mode='oauth_pkce'."
                )
            has_metadata = bool(self.authorization_server_metadata_url)
            has_endpoints = bool(self.authorization_endpoint and self.token_endpoint)
            if not (has_metadata or has_endpoints):
                raise ConfigError(
                    "runtime.mcp_servers[].authorization oauth_pkce requires "
                    "authorization_server_metadata_url or both authorization_endpoint "
                    "and token_endpoint."
                )

    def redacted_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"mode": self.mode}
        if self.mode == "bearer":
            payload["bearer_token"] = "<redacted>" if self.bearer_token else ""
            return payload
        if self.mode == "oauth_pkce":
            payload.update(
                {
                    "client_id": self.client_id,
                    "client_secret_ref": self.client_secret_ref,
                    "authorization_server_metadata_url": (
                        self.authorization_server_metadata_url
                    ),
                    "authorization_endpoint": self.authorization_endpoint,
                    "token_endpoint": self.token_endpoint,
                    "registration_endpoint": self.registration_endpoint,
                    "revocation_endpoint": self.revocation_endpoint,
                    "redirect_uri": self.redirect_uri,
                    "scope": self.scope,
                    "access_token": "<redacted>" if self.access_token else "",
                    "access_token_ref": self.access_token_ref,
                    "refresh_token_ref": self.refresh_token_ref,
                }
            )
        return payload


def _coerce_mcp_authorization_config(value: object) -> MCPAuthorizationConfig:
    if value is None:
        return MCPAuthorizationConfig()
    if isinstance(value, MCPAuthorizationConfig):
        return value
    if isinstance(value, Mapping):
        return MCPAuthorizationConfig(
            mode=value.get("mode", "none"),
            bearer_token=value.get("bearer_token", ""),
            client_id=value.get("client_id", ""),
            client_secret_ref=value.get("client_secret_ref", ""),
            authorization_server_metadata_url=value.get(
                "authorization_server_metadata_url", ""
            ),
            authorization_endpoint=value.get("authorization_endpoint", ""),
            token_endpoint=value.get("token_endpoint", ""),
            registration_endpoint=value.get("registration_endpoint", ""),
            revocation_endpoint=value.get("revocation_endpoint", ""),
            redirect_uri=value.get("redirect_uri", ""),
            scope=value.get("scope", ""),
            access_token=value.get("access_token", ""),
            access_token_ref=value.get("access_token_ref", ""),
            refresh_token_ref=value.get("refresh_token_ref", ""),
        )
    raise ConfigError("runtime.mcp_servers[].authorization must be an object.")


@dataclass
class MCPToolRiskOverrideConfig:
    pattern: str
    min_scope: str = ""
    dangerous: bool | None = None
    idempotent: bool | None = None

    def __post_init__(self) -> None:
        self.pattern = str(self.pattern or "").strip()
        if not self.pattern:
            raise ConfigError(
                "runtime.mcp_servers[].tool_risk_overrides[].pattern is required."
            )
        self.min_scope = str(self.min_scope or "").strip().upper()
        if self.min_scope and self.min_scope not in _VALID_MCP_TOOL_SCOPES:
            raise ConfigError(
                "runtime.mcp_servers[].tool_risk_overrides[].min_scope must be one "
                "of READ_ONLY, WRITE_SAFE, POWER_USER, or UI_AUTOMATION."
            )
        if self.dangerous is not None:
            self.dangerous = bool(self.dangerous)
        if self.idempotent is not None:
            self.idempotent = bool(self.idempotent)


def _coerce_mcp_tool_risk_overrides(
    value: object,
) -> list[MCPToolRiskOverrideConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(
            "runtime.mcp_servers[].tool_risk_overrides must be an array of objects."
        )
    overrides: list[MCPToolRiskOverrideConfig] = []
    for index, item in enumerate(value):
        if isinstance(item, MCPToolRiskOverrideConfig):
            overrides.append(item)
            continue
        if not isinstance(item, Mapping):
            raise ConfigError(
                f"runtime.mcp_servers[].tool_risk_overrides[{index}] must be an object."
            )
        overrides.append(
            MCPToolRiskOverrideConfig(
                pattern=item.get("pattern", ""),
                min_scope=item.get("min_scope", ""),
                dangerous=item.get("dangerous") if "dangerous" in item else None,
                idempotent=item.get("idempotent") if "idempotent" in item else None,
            )
        )
    return overrides


@dataclass
class MCPApprovalConfig:
    mode: str = "never"
    tool_patterns: list[str] = field(default_factory=list)
    risk_levels: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        mode = str(self.mode or "never").strip().lower() or "never"
        if mode not in _VALID_MCP_APPROVAL_MODES:
            raise ConfigError(
                "runtime.mcp_servers[].approval.mode must be one of "
                "'never', 'always', 'dangerous', or 'matching'."
            )
        self.mode = mode
        self.tool_patterns = _normalize_pattern_list(
            self.tool_patterns,
            field_path="runtime.mcp_servers[].approval.tool_patterns",
        )
        self.risk_levels = [
            str(item or "").strip().lower()
            for item in _normalize_pattern_list(
                self.risk_levels,
                field_path="runtime.mcp_servers[].approval.risk_levels",
            )
        ]


def _coerce_mcp_approval_config(value: object) -> MCPApprovalConfig:
    if value is None:
        return MCPApprovalConfig()
    if isinstance(value, MCPApprovalConfig):
        return value
    if isinstance(value, Mapping):
        return MCPApprovalConfig(
            mode=value.get("mode", "never"),
            tool_patterns=list(value.get("tool_patterns", []) or []),
            risk_levels=list(value.get("risk_levels", []) or []),
        )
    raise ConfigError("runtime.mcp_servers[].approval must be an object.")


@dataclass
class MCPStdioSandboxConfig:
    require_trust: bool = False
    cwd_allowlist: list[str] = field(default_factory=list)
    env_allowlist: list[str] = field(default_factory=list)
    package_name: str = ""
    package_version: str = ""
    trust_reason: str = ""

    def __post_init__(self) -> None:
        self.require_trust = bool(self.require_trust)
        self.cwd_allowlist = _normalize_string_list(self.cwd_allowlist)
        self.env_allowlist = _normalize_string_list(self.env_allowlist)
        self.package_name = str(self.package_name or "").strip()
        self.package_version = str(self.package_version or "").strip()
        self.trust_reason = str(self.trust_reason or "").strip()


def _coerce_mcp_stdio_sandbox_config(value: object) -> MCPStdioSandboxConfig:
    if value is None:
        return MCPStdioSandboxConfig()
    if isinstance(value, MCPStdioSandboxConfig):
        return value
    if isinstance(value, Mapping):
        return MCPStdioSandboxConfig(
            require_trust=value.get("require_trust", False),
            cwd_allowlist=list(value.get("cwd_allowlist", []) or []),
            env_allowlist=list(value.get("env_allowlist", []) or []),
            package_name=value.get("package_name", ""),
            package_version=value.get("package_version", ""),
            trust_reason=value.get("trust_reason", ""),
        )
    raise ConfigError("runtime.mcp_servers[].stdio_sandbox must be an object.")


@dataclass
class MCPPackageMetadataConfig:
    """Operator-visible package/provenance metadata for an MCP extension."""

    origin: str = ""
    version: str = ""
    install_command: list[str] = field(default_factory=list)
    trust_state: str = ""

    def __post_init__(self) -> None:
        self.origin = str(self.origin or "").strip()
        self.version = str(self.version or "").strip()
        self.install_command = [
            str(item).strip()
            for item in list(self.install_command or [])
            if str(item).strip()
        ]
        self.trust_state = str(self.trust_state or "").strip().lower()

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "version": self.version,
            "install_command": list(self.install_command),
            "trust_state": self.trust_state,
        }


def _coerce_mcp_package_metadata_config(
    value: object,
) -> MCPPackageMetadataConfig:
    if value is None:
        return MCPPackageMetadataConfig()
    if isinstance(value, MCPPackageMetadataConfig):
        return value
    if isinstance(value, Mapping):
        return MCPPackageMetadataConfig(
            origin=value.get("origin", ""),
            version=value.get("version", ""),
            install_command=list(value.get("install_command", []) or []),
            trust_state=value.get("trust_state", ""),
        )
    raise ConfigError("runtime.mcp_servers[].package_metadata must be an object.")


def resolve_mcp_server_env(
    server: "MCPServerConfig",
    *,
    secret_resolver: Any | None = None,
) -> dict[str, str]:
    """Resolve explicit env values plus secret refs without shell interpolation."""

    resolved = dict(server.env)
    for key, value in resolved.items():
        if _contains_env_interpolation(value):
            raise ConfigError(
                f"runtime.mcp_servers[{server.name!r}].env[{key!r}] contains "
                "unsupported interpolation; use env_secret_refs for secrets."
            )
    for key, secret_ref in server.env_secret_refs.items():
        if secret_resolver is None:
            raise ConfigError(
                f"runtime.mcp_servers[{server.name!r}].env_secret_refs[{key!r}] "
                "requires a secret resolver."
            )
        resolved_value = secret_resolver(secret_ref)
        resolved[key] = str(resolved_value or "")
    return dict(sorted(resolved.items()))


@dataclass
class MCPExposureConfig:
    """Per-agent MCP exposure filter over runtime-level server discovery."""

    include_servers: list[str] = field(default_factory=list)
    exclude_servers: list[str] = field(default_factory=list)
    include_tools: list[str] = field(default_factory=list)
    exclude_tools: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.include_servers = _normalize_server_list(
            self.include_servers,
            field_path="agents.<id>.mcp_exposure.include_servers",
        )
        self.exclude_servers = _normalize_server_list(
            self.exclude_servers,
            field_path="agents.<id>.mcp_exposure.exclude_servers",
        )
        self.include_tools = _normalize_pattern_list(
            self.include_tools,
            field_path="agents.<id>.mcp_exposure.include_tools",
        )
        self.exclude_tools = _normalize_pattern_list(
            self.exclude_tools,
            field_path="agents.<id>.mcp_exposure.exclude_tools",
        )

    @property
    def is_empty(self) -> bool:
        return not (
            self.include_servers
            or self.exclude_servers
            or self.include_tools
            or self.exclude_tools
        )


def coerce_mcp_exposure_config(value: object) -> MCPExposureConfig:
    if value is None:
        return MCPExposureConfig()
    if isinstance(value, MCPExposureConfig):
        return value
    if isinstance(value, Mapping):
        return MCPExposureConfig(
            include_servers=list(value.get("include_servers", []) or []),
            exclude_servers=list(value.get("exclude_servers", []) or []),
            include_tools=list(value.get("include_tools", []) or []),
            exclude_tools=list(value.get("exclude_tools", []) or []),
        )
    raise ConfigError("agents.<id>.mcp_exposure must be an object.")


def mcp_exposure_config_to_dict(config: MCPExposureConfig | None) -> dict[str, Any]:
    exposure = coerce_mcp_exposure_config(config)
    payload: dict[str, Any] = {}
    if exposure.include_servers:
        payload["include_servers"] = list(exposure.include_servers)
    if exposure.exclude_servers:
        payload["exclude_servers"] = list(exposure.exclude_servers)
    if exposure.include_tools:
        payload["include_tools"] = list(exposure.include_tools)
    if exposure.exclude_tools:
        payload["exclude_tools"] = list(exposure.exclude_tools)
    return payload


@dataclass
class MCPPublishConfig:
    """Opt-in OpenMinion-as-MCP-server publication policy."""

    enabled: bool = False
    transport: str = "stdio"
    include_tools: list[str] = field(default_factory=list)
    exclude_tools: list[str] = field(default_factory=list)
    name_prefix: str = "openminion.tool."

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        transport = str(self.transport or "stdio").strip().lower() or "stdio"
        if transport not in _VALID_MCP_PUBLISH_TRANSPORTS:
            raise ConfigError(
                "runtime.mcp_publish.transport must be 'stdio' or 'streamable_http'."
            )
        self.transport = transport
        self.include_tools = _normalize_pattern_list(
            self.include_tools,
            field_path="runtime.mcp_publish.include_tools",
        )
        self.exclude_tools = _normalize_pattern_list(
            self.exclude_tools,
            field_path="runtime.mcp_publish.exclude_tools",
        )
        self.name_prefix = str(self.name_prefix or "openminion.tool.").strip()
        if not self.name_prefix:
            self.name_prefix = "openminion.tool."


def coerce_mcp_publish_config(value: object) -> MCPPublishConfig:
    if value is None:
        return MCPPublishConfig()
    if isinstance(value, MCPPublishConfig):
        return value
    if isinstance(value, Mapping):
        return MCPPublishConfig(
            enabled=value.get("enabled", False),
            transport=value.get("transport", "stdio"),
            include_tools=list(value.get("include_tools", []) or []),
            exclude_tools=list(value.get("exclude_tools", []) or []),
            name_prefix=value.get("name_prefix", "openminion.tool."),
        )
    raise ConfigError("runtime.mcp_publish must be an object.")


def mcp_publish_config_to_dict(config: MCPPublishConfig | None) -> dict[str, Any]:
    publish = coerce_mcp_publish_config(config)
    return {
        "enabled": bool(publish.enabled),
        "transport": publish.transport,
        "include_tools": list(publish.include_tools),
        "exclude_tools": list(publish.exclude_tools),
        "name_prefix": publish.name_prefix,
    }


@dataclass
class MCPServerConfig:
    name: str = ""
    transport: str = "stdio"
    command: list[str] = field(default_factory=list)
    url: str = ""
    authorization: MCPAuthorizationConfig = field(
        default_factory=MCPAuthorizationConfig
    )
    env: dict[str, str] = field(default_factory=dict)
    env_secret_refs: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    startup_timeout_seconds: float = 15.0
    request_timeout_seconds: float = 30.0
    stderr_buffer_bytes: int = 65536
    tool_risk_overrides: list[MCPToolRiskOverrideConfig] = field(default_factory=list)
    approval: MCPApprovalConfig = field(default_factory=MCPApprovalConfig)
    trusted: bool = False
    stdio_sandbox: MCPStdioSandboxConfig = field(default_factory=MCPStdioSandboxConfig)
    package_metadata: MCPPackageMetadataConfig = field(
        default_factory=MCPPackageMetadataConfig
    )

    def __post_init__(self) -> None:
        self.name = normalize_mcp_server_name(self.name)
        self.transport = normalize_mcp_transport(self.transport)
        self.url = _normalize_mcp_url(self.url)
        self.authorization = _coerce_mcp_authorization_config(self.authorization)
        if self.transport == "stdio":
            self.command = _normalize_mcp_command(self.command)
            if self.url:
                raise ConfigError(
                    "runtime.mcp_servers[].url is only valid for "
                    "transport='streamable_http'."
                )
        else:
            self.command = list(self.command or [])
            if not self.url:
                raise ConfigError(
                    "runtime.mcp_servers[].url is required when "
                    "transport='streamable_http'."
                )
        self.env = _normalize_non_empty_string_map(self.env)
        self.env_secret_refs = _normalize_non_empty_string_map(self.env_secret_refs)
        self.cwd = str(self.cwd or "").strip()
        self.tool_risk_overrides = _coerce_mcp_tool_risk_overrides(
            self.tool_risk_overrides
        )
        self.approval = _coerce_mcp_approval_config(self.approval)
        self.trusted = bool(self.trusted)
        self.stdio_sandbox = _coerce_mcp_stdio_sandbox_config(self.stdio_sandbox)
        self.package_metadata = _coerce_mcp_package_metadata_config(
            self.package_metadata
        )
        try:
            self.startup_timeout_seconds = float(self.startup_timeout_seconds)
        except (TypeError, ValueError):
            self.startup_timeout_seconds = 15.0
        try:
            self.request_timeout_seconds = float(self.request_timeout_seconds)
        except (TypeError, ValueError):
            self.request_timeout_seconds = 30.0
        try:
            self.stderr_buffer_bytes = int(self.stderr_buffer_bytes)
        except (TypeError, ValueError):
            self.stderr_buffer_bytes = 65536
        self.startup_timeout_seconds = max(
            1.0, min(120.0, self.startup_timeout_seconds)
        )
        self.request_timeout_seconds = max(
            1.0, min(300.0, self.request_timeout_seconds)
        )
        self.stderr_buffer_bytes = max(1024, min(1048576, self.stderr_buffer_bytes))


def coerce_mcp_server_configs(value: object) -> list[MCPServerConfig]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError("runtime.mcp_servers must be an array of objects.")

    servers: list[MCPServerConfig] = []
    seen_names: set[str] = set()
    for index, item in enumerate(value):
        if isinstance(item, MCPServerConfig):
            server = item
        elif isinstance(item, Mapping):
            server = MCPServerConfig(
                name=item.get("name", ""),
                transport=item.get("transport", "stdio"),
                command=list(item.get("command", []) or []),
                url=item.get("url", ""),
                authorization=_coerce_mcp_authorization_config(
                    item.get("authorization")
                ),
                env=dict(item.get("env", {}) or {}),
                env_secret_refs=dict(item.get("env_secret_refs", {}) or {}),
                cwd=str(item.get("cwd", "") or "").strip(),
                startup_timeout_seconds=item.get("startup_timeout_seconds", 15.0),
                request_timeout_seconds=item.get("request_timeout_seconds", 30.0),
                stderr_buffer_bytes=item.get("stderr_buffer_bytes", 65536),
                tool_risk_overrides=list(item.get("tool_risk_overrides", []) or []),
                approval=_coerce_mcp_approval_config(item.get("approval")),
                trusted=item.get("trusted", False),
                stdio_sandbox=_coerce_mcp_stdio_sandbox_config(
                    item.get("stdio_sandbox")
                ),
                package_metadata=_coerce_mcp_package_metadata_config(
                    item.get("package_metadata")
                ),
            )
        else:
            raise ConfigError(
                f"runtime.mcp_servers[{index}] must be an object describing one server."
            )
        if server.name in seen_names:
            raise ConfigError(
                "runtime.mcp_servers names must be unique after normalization: "
                f"{server.name!r}"
            )
        seen_names.add(server.name)
        servers.append(server)
    return servers


__all__ = [
    "MCPAuthorizationConfig",
    "MCPApprovalConfig",
    "MCPExposureConfig",
    "MCPPackageMetadataConfig",
    "MCPPublishConfig",
    "MCPServerConfig",
    "MCPStdioSandboxConfig",
    "MCPToolRiskOverrideConfig",
    "coerce_mcp_exposure_config",
    "coerce_mcp_publish_config",
    "coerce_mcp_server_configs",
    "mcp_publish_config_to_dict",
    "mcp_exposure_config_to_dict",
    "normalize_mcp_server_name",
    "normalize_mcp_sampling_mode",
    "normalize_mcp_tool_segment",
    "normalize_mcp_transport",
    "resolve_mcp_server_env",
]
