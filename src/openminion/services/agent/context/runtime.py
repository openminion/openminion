from dataclasses import dataclass

from openminion.base.types import Message
from openminion.modules.llm.providers.base import ProviderHistoryMessage

from .grounding import append_grounding_blocks, build_grounding_facts
from .history import _map_history_to_provider, _resolve_system_prompt
from openminion.modules.prompting.context_blocks import build_project_context_block


@dataclass(frozen=True)
class ContextBuildResult:
    system_prompt: str
    provider_history: list[ProviderHistoryMessage]
    user_message: str
    untrusted_metadata: dict[str, str]
    untrusted_events: list[dict[str, str]]


def build_context(
    *,
    service,
    inbound: Message,
    history: list[Message] | None,
) -> ContextBuildResult:
    inbound_metadata = dict(inbound.metadata or {})
    system_prompt = _resolve_system_prompt(service._config)
    system_prompt = service._inject_identity_system_prompt(
        system_prompt=system_prompt,
        inbound_metadata=inbound_metadata,
    )
    system_prompt = append_grounding_blocks(
        system_prompt=system_prompt,
        facts=build_grounding_facts(
            runtime_env=getattr(getattr(service._config, "runtime", None), "env", None),
            home_root=getattr(service, "_home_root", None),
            workspace_root=getattr(service, "workspace_root", None),
            inbound_metadata=inbound_metadata,
            tools=getattr(service, "_tools", None),
            include_session_working_state=False,
        ),
    )
    system_prompt = _append_project_context_block(
        system_prompt=system_prompt,
        inbound_metadata=inbound_metadata,
    )
    provider_history = _map_history_to_provider(history or [])
    user_message = inbound.body
    untrusted_metadata: dict[str, str] = {}
    untrusted_events: list[dict[str, str]] = []
    if str(inbound.metadata.get("untrusted_input", "")).strip().lower() == "true":
        untrusted_source = str(inbound.metadata.get("untrusted_source", "")).strip()
        user_message = (
            "[UNTRUSTED CONTENT BEGIN]\n"
            f"source={untrusted_source}\n"
            f"{user_message}\n"
            "[UNTRUSTED CONTENT END]"
        )
        untrusted_metadata["untrusted_content_wrapped"] = "true"
        if untrusted_source:
            untrusted_metadata["untrusted_content_source"] = untrusted_source
        untrusted_events.append(
            {
                "event_kind": "security_warning",
                "reason_code": "untrusted_suspicious_input",
                "policy_version": "v1",
                "decision": "warn",
                "source": untrusted_source,
            }
        )
    return ContextBuildResult(
        system_prompt=system_prompt,
        provider_history=provider_history,
        user_message=user_message,
        untrusted_metadata=untrusted_metadata,
        untrusted_events=untrusted_events,
    )


def _append_project_context_block(
    *,
    system_prompt: str,
    inbound_metadata: dict[str, str],
) -> str:
    block = build_project_context_block(inbound_metadata=inbound_metadata)
    return "\n\n".join(part for part in (system_prompt, block) if part).strip()
