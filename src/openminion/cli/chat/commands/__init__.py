from __future__ import annotations

from typing import Any

from .base import (
    ChatCommandHandlers,
    ChatCommandResult,
    check_search_available,
    handle_chat_command as _handle_chat_command_impl,
)
from .context import (
    _get_identity_debug_info,
    _get_introspection_debug_info,
    _get_module_usage_debug_info,
    _get_reactions_debug_info,
    _get_rlm_debug_info,
    _get_search_provider_debug_info,
    _get_search_provider_info,
    _get_telemetry_debug_info,
    _handle_debug_command as _handle_debug_command_impl,
    _print_debug_context,
    _print_module_debug,
    _resolve_search_provider,
)
from .message import (
    _extract_skill_name_from_url,
    _extract_skill_source,
    _fetch_skill_from_url,
    _handle_skill_command,
    _is_blocked_skill_host,
    _is_valid_markdown_content,
    _run_chat_skill_ingest,
    _run_chat_skill_ingest_url,
    _run_chat_skill_list,
    _run_chat_skill_remove,
)
from .session import (
    _build_identityctl,
    _handle_agent_inspect,
    _handle_grants_command,
    _handle_identity_command as _handle_identity_command_impl,
    _handle_pair_create,
    _handle_pair_revoke,
    _handle_pair_status,
    _handle_policy_command,
    _handle_trust_command,
    _handle_untrust_command,
    _print_identity_help,
    _resolve_identity_db_path,
)
from .utility import _handle_sidecar_command, _print_sidecar_help, _print_tools


def _handle_debug_command(
    *,
    line: str,
    config,
    agent_id: str,
    session_id: str,
    transport: str,
    last_turn_debug: dict[str, Any],
    endpoint,
) -> None:
    return _handle_debug_command_impl(
        line=line,
        config=config,
        agent_id=agent_id,
        session_id=session_id,
        transport=transport,
        last_turn_debug=last_turn_debug,
        endpoint=endpoint,
        print_debug_context_fn=_print_debug_context,
        print_module_debug_fn=_print_module_debug,
    )


def _handle_identity_command(*, line: str, config, agent_id: str) -> None:
    return _handle_identity_command_impl(
        line=line,
        config=config,
        agent_id=agent_id,
        build_identityctl_fn=_build_identityctl,
    )


def handle_chat_command(
    *,
    line: str,
    args,
    config,
    agent_id: str,
    session_id: str,
    transport: str,
    mode: str,
    runtime_state,
    last_artifacts: list[dict],
    last_turn_debug: dict[str, Any],
) -> ChatCommandResult:
    return _handle_chat_command_impl(
        line=line,
        args=args,
        config=config,
        agent_id=agent_id,
        session_id=session_id,
        transport=transport,
        mode=mode,
        runtime_state=runtime_state,
        last_artifacts=last_artifacts,
        last_turn_debug=last_turn_debug,
        handlers=ChatCommandHandlers(
            print_tools=_print_tools,
            handle_debug_command=_handle_debug_command,
            handle_pair_status=_handle_pair_status,
            handle_pair_create=_handle_pair_create,
            handle_pair_revoke=_handle_pair_revoke,
            handle_trust_command=_handle_trust_command,
            handle_untrust_command=_handle_untrust_command,
            handle_grants_command=_handle_grants_command,
            handle_policy_command=_handle_policy_command,
            handle_skill_command=_handle_skill_command,
            handle_identity_command=_handle_identity_command,
            handle_sidecar_command=_handle_sidecar_command,
        ),
    )


__all__ = [
    "ChatCommandHandlers",
    "ChatCommandResult",
    "check_search_available",
    "handle_chat_command",
    "_extract_skill_source",
    "_handle_skill_command",
    "_is_blocked_skill_host",
    "_is_valid_markdown_content",
    "_extract_skill_name_from_url",
    "_fetch_skill_from_url",
    "_run_chat_skill_ingest",
    "_run_chat_skill_remove",
    "_run_chat_skill_ingest_url",
    "_run_chat_skill_list",
    "_get_reactions_debug_info",
    "_get_module_usage_debug_info",
    "_get_identity_debug_info",
    "_get_telemetry_debug_info",
    "_get_search_provider_info",
    "_resolve_search_provider",
    "_get_search_provider_debug_info",
    "_get_rlm_debug_info",
    "_get_introspection_debug_info",
    "_print_debug_context",
    "_print_module_debug",
    "_handle_debug_command",
    "_print_tools",
    "_handle_pair_status",
    "_handle_pair_create",
    "_handle_pair_revoke",
    "_handle_trust_command",
    "_handle_untrust_command",
    "_handle_grants_command",
    "_handle_policy_command",
    "_handle_agent_inspect",
    "_resolve_identity_db_path",
    "_build_identityctl",
    "_print_identity_help",
    "_handle_identity_command",
    "_handle_sidecar_command",
    "_print_sidecar_help",
]
