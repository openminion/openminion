import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.modules.llm.reasoning import (
    ThinkingCtl,
    ThinkingRequest,
    ThinkingResolutionInput,
)
from openminion.modules.llm.constants import (
    LLM_TOOL_CALL_STRATEGY_NATIVE,
    LLM_TOOL_CHOICE_AUTO,
)

from openminion.modules.llm.providers.base import (
    LLMProvider,
    PROVIDER_RESPONSE_INTERFACE_VERSION,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ensure_provider_response_compatibility,
)
from openminion.modules.llm.providers.behavior import (
    resolve_behavior_profile,
)
from openminion.modules.llm.providers.normalization import (
    normalize_provider_response,
)
from openminion.modules.llm.providers.tool_choice import (
    complete_with_provider_override_retry,
)


def _import_openminion_llm() -> tuple[Any, Any, Any]:
    try:
        from openminion.modules.llm import LLMCTL
        from openminion.modules.llm.config import AgentProfile, ToolPolicy

        return AgentProfile, LLMCTL, ToolPolicy
    except ModuleNotFoundError:
        provider_path = Path(__file__).resolve()
        workspace_root = provider_path.parents[4]
        candidate_src_paths = [workspace_root / "openminion" / "src"]
        for llmctl_src in candidate_src_paths:
            if not llmctl_src.exists():
                continue
            llmctl_src_str = str(llmctl_src)
            if llmctl_src_str not in sys.path:
                sys.path.insert(0, llmctl_src_str)

        from openminion.modules.llm import LLMCTL
        from openminion.modules.llm.config import AgentProfile, ToolPolicy

        return AgentProfile, LLMCTL, ToolPolicy


def _normalize_bridge_submit_output_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if str(tool_name or "").strip() != "submit_output":
        decoded = _decode_bridge_json_like(dict(arguments or {}))
        return decoded if isinstance(decoded, dict) else dict(arguments or {})
    args = dict(arguments or {})
    for key in ("decision", "Decision", "output", "result", "payload"):
        raw = args.get(key)
        if isinstance(raw, dict):
            decoded = _decode_bridge_json_like(dict(raw))
            return decoded if isinstance(decoded, dict) else dict(raw)
        if isinstance(raw, str):
            token = raw.strip()
            if token.startswith("{"):
                try:
                    parsed = json.loads(token)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    decoded = _decode_bridge_json_like(dict(parsed))
                    return decoded if isinstance(decoded, dict) else dict(parsed)
    decoded_args = _decode_bridge_json_like(args)
    return decoded_args if isinstance(decoded_args, dict) else args


def _decode_bridge_json_like(value: Any) -> Any:
    if isinstance(value, str):
        token = value.strip()
        if token.startswith("{") or token.startswith("["):
            try:
                parsed = json.loads(token)
            except json.JSONDecodeError:
                return value
            return _decode_bridge_json_like(parsed)
        return value
    if isinstance(value, dict):
        return {key: _decode_bridge_json_like(raw) for key, raw in value.items()}
    if isinstance(value, list):
        return [_decode_bridge_json_like(item) for item in value]
    return value


class LLMCTLBridgeProvider(LLMProvider):
    """OpenMinion provider adapter backed by openminion.modules.llm runtime."""

    contract_version = PROVIDER_RESPONSE_INTERFACE_VERSION

    def __init__(
        self,
        *,
        provider_name: str,
        model: str,
        provider_config: Dict[str, Any],
        env: EnvironmentConfig | Mapping[str, object] | None = None,
    ) -> None:
        AgentProfile, LLMCTL, ToolPolicy = _import_openminion_llm()
        self.name = provider_name
        self._model = model.strip() or "stub-v1"
        self._provider_config = dict(provider_config)
        self._env = resolve_environment_config(env=env)
        self._thinking_ctl = ThinkingCtl()
        request_timeout_sec = _resolve_bridge_request_timeout_seconds(
            provider_name=self.name,
            provider_config=self._provider_config,
        )
        max_retries = _resolve_bridge_max_retries(provider_name=self.name)

        llmctl_config = {
            "version": 1,
            "llmctl": {
                "default_provider": self.name,
                "default_model": self._model,
                "timeouts": {
                    "request_timeout_sec": request_timeout_sec,
                    "connect_timeout_sec": 10,
                },
                "retries": {"max_retries": max_retries, "backoff_ms": 300},
                "logging": {"redaction": "normal", "include_provider_raw": False},
            },
            "providers": {
                self.name: dict(provider_config),
            },
            "agents": {
                "openminion_bridge": {
                    "default_provider": self.name,
                    "default_model": self._model,
                    "tool_policy": {
                        "enable_tools": True,
                        "allowed_tools": None,
                        "tool_choice_default": LLM_TOOL_CHOICE_AUTO,
                        "block_on_disallowed_tool_call": False,
                    },
                }
            },
        }

        self._runtime = LLMCTL.from_config(llmctl_config)
        profile = AgentProfile(
            name="openminion_bridge",
            default_provider=self.name,
            default_model=self._model,
            tool_policy=ToolPolicy(
                enable_tools=True,
                allowed_tools=None,
                tool_choice_default=LLM_TOOL_CHOICE_AUTO,
                block_on_disallowed_tool_call=False,
            ),
        )
        self._client = self._runtime.client(profile=profile)
        ensure_provider_response_compatibility(
            self, component_name=f"llmctl-bridge:{self.name}"
        )

    def _normalize_request_thinking(self, request: ProviderRequest) -> ProviderRequest:
        metadata = {
            str(key): str(value)
            for key, value in dict(getattr(request, "metadata", {}) or {}).items()
        }
        if metadata.get("thinking_reasoning_profile"):
            request.metadata = metadata
            if not str(getattr(request, "thinking", "") or "").strip():
                request.thinking = str(
                    metadata.get("thinking_provider_effort")
                    or metadata.get("thinking")
                    or ""
                ).strip()
            return request

        requested_profile = (
            str(getattr(request, "thinking", "") or "").strip()
            or str(
                metadata.get("thinking_requested_profile")
                or metadata.get("thinking_reasoning_profile")
                or metadata.get("thinking")
                or ""
            ).strip()
        )
        resolved = self._thinking_ctl.resolve(
            request=ThinkingRequest(
                purpose=str(metadata.get("purpose", "") or "").strip() or None,
                requested_profile=requested_profile or None,
                provider=self.name or None,
                model=self._model or None,
                metadata=metadata,
            ),
            layers=ThinkingResolutionInput(
                code_default_profile="minimal",
            ),
        )
        metadata.update(self._thinking_ctl.build_provider_metadata(resolved=resolved))
        request.metadata = metadata
        request.thinking = str(resolved.provider_effort or "")
        return request

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        request = self._normalize_request_thinking(request)
        messages: list[dict[str, str]] = []
        if str(request.system_prompt or "").strip():
            messages.append({"role": "system", "content": str(request.system_prompt)})

        for item in request.history:
            role = str(item.role or "").strip().lower()
            if role not in {"system", "user", "assistant", "tool"}:
                role = "user"
            content = str(item.content or "").strip()
            if not content:
                continue
            payload = {"role": role, "content": content}
            meta = dict(getattr(item, "meta", {}) or {})
            if meta:
                payload["meta"] = meta
            messages.append(payload)

        messages.append({"role": "user", "content": str(request.user_message)})

        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": dict(tool.parameters or {}),
                "strict": bool(tool.strict),
            }
            for tool in request.tools
            if str(tool.name or "").strip()
        ]

        metadata = {str(k): str(v) for k, v in dict(request.metadata or {}).items()}
        if str(request.tool_call_strategy or "").strip():
            metadata["tool_call_strategy"] = str(request.tool_call_strategy)
        behavior_profile = resolve_behavior_profile(
            provider=self.name,
            model=self._model,
            base_url=str(self._provider_config.get("base_url") or ""),
            provider_identity=self._provider_config.get("provider_identity"),
            metadata=metadata,
            env=self._env,
        )

        completion_result = await asyncio.to_thread(
            complete_with_provider_override_retry,
            complete_fn=self._client.complete,
            provider_name=self.name,
            model_name=self._model,
            messages=messages,
            tools=tools or None,
            tool_choice=request.tool_choice,
            metadata=metadata,
            thinking=request.thinking,
            env=self._env,
            policy=behavior_profile.retry_override_policy,
        )
        retry_override_id = completion_result.retry_override_id
        response = completion_result.response
        allowed_tool_names = [
            tool["name"] for tool in tools if str(tool.get("name", "")).strip()
        ]
        if not response.ok:
            error_code = str(getattr(response.error, "code", "") or "").strip()
            error_message = str(getattr(response.error, "message", "") or "").strip()
            if error_code == "EMPTY_PAYLOAD":
                recovered_text = _extract_bridge_text(response)
                synthesized = ProviderResponse(
                    text=recovered_text,
                    model=str(getattr(response, "model", "") or self._model),
                    usage={},
                    tool_calls=[],
                    finish_reason=str(getattr(response, "finish_reason", "") or ""),
                    normalization={
                        "adapter": "llmctl_bridge",
                        "behavior_profile_id": behavior_profile.profile_id,
                        "upstream_error_code": error_code,
                        "upstream_error_message": error_message,
                    },
                )
                return normalize_provider_response(
                    synthesized,
                    provider_name=self.name,
                    model_name=self._model,
                    allowed_tool_names=allowed_tool_names,
                    profile=behavior_profile.normalization_profile,
                )

            if response.error is None:
                raise ProviderError("llmctl bridge call failed")
            raise ProviderError(f"{response.error.code}: {response.error.message}")

        usage: dict[str, int] = {}
        if response.usage.input_tokens is not None:
            usage["prompt_tokens"] = int(response.usage.input_tokens)
        if response.usage.output_tokens is not None:
            usage["completion_tokens"] = int(response.usage.output_tokens)
        if response.usage.total_tokens is not None:
            usage["total_tokens"] = int(response.usage.total_tokens)

        tool_calls: list[ProviderToolCall] = []
        for call in response.tool_calls:
            name = str(call.name or "").strip()
            if not name:
                continue
            arguments = _normalize_bridge_submit_output_arguments(
                name,
                dict(call.arguments or {}),
            )
            tool_calls.append(
                ProviderToolCall(
                    id=str(call.id or ""),
                    name=name,
                    arguments=arguments,
                    source=str(call.status or LLM_TOOL_CALL_STRATEGY_NATIVE),
                )
            )

        finish_reason = str(getattr(response, "finish_reason", "") or "")

        raw_thinking = getattr(response, "thinking", None) or []
        raw_bridge_response = ProviderResponse(
            text=_extract_bridge_text(response),
            model=str(response.model or self._model),
            usage=usage,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            normalization={
                "adapter": "llmctl_bridge",
                "behavior_profile_id": behavior_profile.profile_id,
                "llm_response_contract_version": str(
                    getattr(response, "contract_version", "v1")
                ),
                "provider_retry_override": retry_override_id,
            },
            thinking=list(raw_thinking),
        )
        return normalize_provider_response(
            raw_bridge_response,
            provider_name=self.name,
            model_name=self._model,
            allowed_tool_names=allowed_tool_names,
            profile=behavior_profile.normalization_profile,
        )


def llmctl_bridge_available(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> bool:
    env_owner = resolve_environment_config(env=env)
    if env_owner.openminion_disable_llmctl_bridge:
        return False
    try:
        _import_openminion_llm()
        return True
    except Exception:
        return False


def _resolve_bridge_request_timeout_seconds(
    *, provider_name: str, provider_config: Dict[str, Any]
) -> int:
    base_timeout = _as_positive_int(provider_config.get("timeout_seconds"), default=60)
    if provider_name != "cortensor":
        return base_timeout

    precommit_timeout = _as_non_negative_int(
        provider_config.get("precommit_timeout_seconds"), default=0
    )
    timeout_buffer = _as_non_negative_int(
        provider_config.get("transport_timeout_buffer_seconds"), default=0
    )
    timeout_headroom = max(timeout_buffer, 60)
    payload_timeout_floor = precommit_timeout + timeout_headroom
    transport_timeout_floor = payload_timeout_floor + timeout_buffer
    return max(base_timeout, transport_timeout_floor, 1)


def _resolve_bridge_max_retries(*, provider_name: str) -> int:
    if provider_name == "cortensor":
        return 0
    return 2


def _as_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    if parsed <= 0:
        return int(default)
    return parsed


def _as_non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    if parsed < 0:
        return int(default)
    return parsed


def _extract_bridge_text(response: Any) -> str:
    output_text = _extract_text_like(getattr(response, "output_text", ""))
    if output_text:
        return output_text

    assistant_chunks: list[str] = []
    for message in list(getattr(response, "assistant_messages", []) or []):
        role = str(getattr(message, "role", "")).strip().lower()
        if role and role != "assistant":
            continue
        content_text = _extract_text_like(getattr(message, "content", ""))
        if content_text:
            assistant_chunks.append(content_text)
    return "\n".join(chunk for chunk in assistant_chunks if chunk).strip()


def _extract_text_like(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()

    if isinstance(value, dict):
        for key in ("text", "output_text", "content", "value"):
            nested = _extract_text_like(value.get(key))
            if nested:
                return nested
        return ""

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        chunks: list[str] = []
        for item in value:
            nested = _extract_text_like(item)
            if nested:
                chunks.append(nested)
        return "\n".join(chunks).strip()

    return ""
