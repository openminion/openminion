"""OpenAI-dialect request compatibility profiles."""

from dataclasses import dataclass
from typing import Any
from collections.abc import Mapping

from openminion.modules.llm.providers.behavior.constants import (
    DEFAULT_REQUEST_DIALECT,
    MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT,
)

from ...constants import LLM_TOOL_CHOICE_REQUIRED


@dataclass(frozen=True)
class OpenAIRequestCompatProfile:
    profile_id: str = "openai_default"
    collapse_system_messages: bool = False
    disable_fallback_instruction: bool = False
    native_tool_only_instruction: str = ""
    enable_structured_tool_envelope_parse: bool = False
    retry_empty_payload_once: bool = False
    empty_payload_retry_instruction: str = ""


def resolve_openai_request_compat(
    *,
    provider_identity: Mapping[str, Any] | None = None,
    request_dialect: str = DEFAULT_REQUEST_DIALECT,
) -> OpenAIRequestCompatProfile:
    normalized_request_dialect = str(request_dialect or "").strip().lower()
    if (
        normalized_request_dialect == MINIMAX_OPENAI_COMPAT_REQUEST_DIALECT
        or _provider_identity_uses_minimax_compat(provider_identity)
    ):
        return OpenAIRequestCompatProfile(
            profile_id="minimax_openai_compat",
            collapse_system_messages=True,
            disable_fallback_instruction=True,
            enable_structured_tool_envelope_parse=True,
            retry_empty_payload_once=True,
            native_tool_only_instruction=(
                "Native tool-calling contract:\n"
                "1. When tools are needed, emit native API tool calls only.\n"
                "2. Do not describe tool calls in prose, JSON text, XML, or markdown.\n"
                "3. If the user explicitly requires tool-backed evidence or cited "
                "sources, do not answer from memory before using the tools."
            ),
            empty_payload_retry_instruction=(
                "Retry contract:\n"
                "1. Return either visible assistant text in message.content or native "
                "API tool calls.\n"
                "2. Never leave the assistant message empty.\n"
                "3. Do not place the final answer only in reasoning-only fields.\n"
                "4. If you are finished, provide the answer as normal assistant text."
            ),
        )
    return OpenAIRequestCompatProfile()


def requires_auto_tool_choice_compat(tool_choice: Any) -> bool:
    if isinstance(tool_choice, str):
        return tool_choice.strip().lower() == LLM_TOOL_CHOICE_REQUIRED
    return isinstance(tool_choice, dict)


def _provider_identity_uses_minimax_compat(
    provider_identity: Mapping[str, Any] | None,
) -> bool:
    if not isinstance(provider_identity, Mapping):
        return False
    if str(provider_identity.get("transport_adapter") or "").strip() != "openai_chat":
        return False
    if (
        str(provider_identity.get("wire_protocol_family") or "").strip()
        != "openai_chat_completions"
    ):
        return False
    if str(provider_identity.get("model_family") or "").strip() != "minimax":
        return False
    return str(provider_identity.get("service_vendor") or "").strip() in {
        "minimax",
        "dashscope",
    }
