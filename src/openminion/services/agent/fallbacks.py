import json
from typing import Any, Iterable, Mapping, Optional

from openminion.base.types import Message
from openminion.modules.llm.providers.base import ProviderToolSpec, ProviderToolCall
from openminion.modules.tool.runtime.routing import build_runtime_tool_routing_metadata

from .errors import (
    normalize_required_tool_arguments,
    sanitize_arguments_for_spec,
)

_FALLBACK_ALLOWED_TOKENS: frozenset[str] = frozenset(
    {
        "tool_unavailable",
        "transient_network_error",
        "provider_empty",
        "validation_exhausted",
        "rate limit",
        "timeout",
        "not found",
        "unavailable",
        "failed to reach",
        "connection refused",
        "connection error",
        "executable doesn't exist",
        "backend_error",
        "temporarily",
    }
)

_FALLBACK_DISALLOWED_TOKENS: frozenset[str] = frozenset(
    {
        "policy_denied",
        "permission",
        "forbidden",
        "auth",
        "unauthorized",
        "approval",
        "safety",
        "blocked",
        "quota_exceeded",
    }
)


def _normalized_tokens(values: Iterable[Any]) -> tuple[str, ...]:
    tokens: list[str] = []
    for value in values:
        token = str(value or "").strip().lower()
        if token and token not in tokens:
            tokens.append(token)
    return tuple(tokens)


def _structured_error_values(data: Mapping[str, Any]) -> tuple[str, ...]:
    return _normalized_tokens(
        data.get(key)
        for key in (
            "error_code",
            "reason_code",
            "code",
            "category",
            "state",
        )
    )


def _matches_structured_error(token: str, values: tuple[str, ...]) -> bool:
    if not token:
        return False
    return any(token in value for value in values)


class AgentToolFallbacksMixin:
    def _configured_fallback_tokens(
        self, *, field_name: str, defaults: Iterable[str]
    ) -> tuple[str, ...]:
        tool_selection = getattr(
            getattr(getattr(self, "_config", None), "runtime", None),
            "tool_selection",
            None,
        )
        configured = getattr(tool_selection, field_name, None)
        if configured is None:
            configured = defaults
        return _normalized_tokens(configured)

    @staticmethod
    def _extract_explicit_tool_arguments(
        *,
        tool_name: str,
        message: str,
    ) -> Optional[dict[str, Any]]:
        text = str(message or "").strip()
        if not text.lower().startswith("tool "):
            return None
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            return None
        explicit_name = str(parts[1] or "").strip()
        if explicit_name.lower() != str(tool_name or "").strip().lower():
            return None
        if len(parts) < 3 or not str(parts[2] or "").strip():
            return {}
        payload = str(parts[2]).strip()
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, dict):
            return decoded
        return {"value": decoded}

    def _fallback_eligibility_reason(self, result: Any) -> str | None:
        if getattr(result, "ok", False):
            return None

        data = getattr(result, "data", {}) or {}
        if not isinstance(data, Mapping):
            return None
        structured_values = _structured_error_values(data)
        if not structured_values:
            return None

        for token in self._configured_fallback_tokens(
            field_name="runtime_no_fallback_on",
            defaults=_FALLBACK_DISALLOWED_TOKENS,
        ):
            if _matches_structured_error(token, structured_values):
                return None

        for token in self._configured_fallback_tokens(
            field_name="runtime_fallback_on",
            defaults=_FALLBACK_ALLOWED_TOKENS,
        ):
            if _matches_structured_error(token, structured_values):
                return token

        return None

    def _should_retry_with_fallback(self, result: Any) -> bool:
        return self._fallback_eligibility_reason(result) is not None

    def _augment_browser_fallback_chain(
        self, *, fallback_chain: list[str]
    ) -> list[str]:
        chain: list[str] = []
        seen: set[str] = set()
        for item in fallback_chain:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            chain.append(token)
            seen.add(token)
        return chain

    def _execute_direct_tool_fallback(
        self,
        *,
        tool_name: str,
        spec: Optional[ProviderToolSpec],
        inbound: Message,
    ):
        if not tool_name or self._tools is None:
            return None

        from openminion.modules.tool.base import ToolExecutionContext

        tool_metadata = dict(inbound.metadata or {})
        runtime_env = getattr(
            getattr(getattr(self, "_config", None), "runtime", None),
            "env",
            None,
        )
        if isinstance(runtime_env, Mapping):
            tool_metadata.setdefault("runtime_env", dict(runtime_env))
        runtime_tools = getattr(
            getattr(getattr(self, "_config", None), "runtime", None),
            "tools",
            None,
        )
        for key, value in build_runtime_tool_routing_metadata(runtime_tools).items():
            tool_metadata.setdefault(key, value)
        agent_id = str(getattr(self, "_identity_agent_id", "") or "").strip()
        if agent_id:
            tool_metadata.setdefault("agent_id", agent_id)
        tool_selection = getattr(self, "_tool_selection", None)
        if tool_selection is not None:
            for key, value in tool_selection.runtime_binding_policy_metadata().items():
                tool_metadata.setdefault(key, value)
        from openminion.services.security.blast_radius.wiring import (
            SEAM_AGENT_TOOL_FALLBACKS,
            build_default_composition_boundary_adapter,
        )

        ctx = ToolExecutionContext(
            channel=inbound.channel,
            target=inbound.target,
            session_id=inbound.metadata.get("session_id", ""),
            metadata=tool_metadata,
            blast_radius_adapter=build_default_composition_boundary_adapter(
                seam_id=SEAM_AGENT_TOOL_FALLBACKS,
            ),
        )

        arguments = self._build_direct_fallback_arguments(
            tool_name=tool_name,
            spec=spec,
            inbound=inbound,
        )
        if arguments is None:
            return None
        return self._tools.execute_calls(
            [
                ProviderToolCall(
                    name=tool_name,
                    arguments=arguments,
                    source="agent_direct_fallback",
                )
            ],
            context=ctx,
        )

    def _build_direct_fallback_arguments(
        self,
        *,
        tool_name: str,
        spec: Optional[ProviderToolSpec],
        inbound: Message,
    ) -> Optional[dict[str, Any]]:
        message = str(inbound.body or "").strip()
        if not message:
            return None

        explicit_arguments = self._extract_explicit_tool_arguments(
            tool_name=tool_name,
            message=message,
        )
        if explicit_arguments is not None:
            return explicit_arguments

        del tool_name, spec
        return None

    def _normalize_required_tool_arguments(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        return normalize_required_tool_arguments(
            tool_name=tool_name, arguments=arguments
        )

    @staticmethod
    def _sanitize_arguments_for_spec(
        *,
        arguments: Mapping[str, Any],
        spec: Optional[ProviderToolSpec],
    ) -> dict[str, Any]:
        return sanitize_arguments_for_spec(arguments=arguments, spec=spec)

    def _build_required_tool_retry_prompt(
        self,
        *,
        user_message: str,
        tool_name: str,
        spec: ProviderToolSpec,
    ) -> str:
        return build_required_tool_retry_prompt(
            user_message=user_message,
            tool_name=tool_name,
            spec=spec,
        )


from .errors import build_required_tool_retry_prompt  # noqa: E402


class AgentToolFallbacks(AgentToolFallbacksMixin):
    """Explicit fallback collaborator for AgentService runtime composition."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._owner, name)
