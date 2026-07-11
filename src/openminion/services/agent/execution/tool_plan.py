from typing import Any, Callable, Optional

from openminion.base.types import Message

from .composition import build_service_port
from .ports import TurnFlowServicePort
from .state import ToolPlan

# intent_router_rules removed - use LLM-driven routing
NO_INTENT_CATEGORY = "none"

Canonicalize = Callable[[str], str]
CanonicalizeChain = Callable[[list[str]], list[str]]


def _infer_forced_tools_from_message(
    *,
    user_message: str,
    canonical_tool_name: Canonicalize,
) -> list[str]:
    stripped = str(user_message or "").strip()
    lowered = stripped.lower()
    if not stripped:
        return []

    if lowered.startswith("tool "):
        parts = stripped.split(maxsplit=2)
        if len(parts) >= 2:
            token = canonical_tool_name(parts[1])
            if token:
                return [token]

    return []


def _service_port(service_or_port: Any) -> TurnFlowServicePort:
    if isinstance(service_or_port, TurnFlowServicePort):
        return service_or_port
    return build_service_port(service_or_port)


def _resolve_explicit_forced_tools(
    service_port: TurnFlowServicePort,
    *,
    effective_forced_tools: list[str],
    canonical_tool_name: Canonicalize,
    canonical_tool_chain: CanonicalizeChain,
) -> tuple[list[str], list[str], str | None]:
    available_forced = [
        canonical_tool_name(name)
        for name in effective_forced_tools
        if service_port.get_spec_for_tool(name) is not None
    ]
    available_forced = canonical_tool_chain(available_forced)
    if not available_forced:
        return effective_forced_tools, [], "forced_tool_unavailable"
    return available_forced, list(available_forced[1:]), None


def _resolve_capability_tools(
    service_port: TurnFlowServicePort,
    *,
    inbound: Message,
    intent_category: str,
    canonical_tool_chain: CanonicalizeChain,
) -> tuple[list[str], list[str]]:
    tool_selection = service_port.tool_selection
    if tool_selection is None:
        return [], []

    shortlist_applied = False
    effective_forced_tools: list[str] = []
    fallback_chain: list[str] = []
    try:
        selection = tool_selection.select_tools(
            query=inbound.body,
            intent_categories=[intent_category],
            forced_category=intent_category,
            identity_tool_filter=service_port.identity_tool_filter,
        )
        if selection.shortlist:
            effective_forced_tools = canonical_tool_chain([selection.shortlist[0]])
            fallback_chain = canonical_tool_chain(list(selection.shortlist[1:]))
            if not fallback_chain:
                fallback_chain = canonical_tool_chain(
                    tool_selection.get_fallback_tools_for_category(intent_category)
                )
            shortlist_applied = True
    except Exception as exc:  # noqa: BLE001
        logger = service_port.logger
        if logger is not None:
            logger.debug("tool shortlist selection failed reason=%s", exc)

    if not shortlist_applied:
        primary = tool_selection.get_primary_tool_for_category(intent_category)
        if primary:
            effective_forced_tools = canonical_tool_chain([primary])
            fallback_chain = canonical_tool_chain(
                tool_selection.get_fallback_tools_for_category(intent_category)
            )
    return effective_forced_tools, fallback_chain


def build_tool_plan(
    service_or_port: Any,
    *,
    inbound: Message,
    user_message: str,
    forced_tools: list[str] | None,
    capability_category: Optional[str],
    canonical_tool_name: Canonicalize,
    canonical_tool_chain: CanonicalizeChain,
) -> ToolPlan:
    service_port = _service_port(service_or_port)
    updated_user_message = str(user_message or "")
    effective_forced_tools = list(forced_tools or [])
    if not effective_forced_tools:
        effective_forced_tools = _infer_forced_tools_from_message(
            user_message=updated_user_message,
            canonical_tool_name=canonical_tool_name,
        )

    # LAIR: LLM-driven intent routing.
    intent_category = capability_category if capability_category else None

    explicit_forced_tools = bool(effective_forced_tools)
    explicit_capability = bool(
        capability_category and str(capability_category).strip().lower() != ""
    )

    requested_forced_tools = list(effective_forced_tools)
    fallback_chain: list[str] = []
    capability_primary: str | None = None
    unavailable_reason: str | None = None

    if explicit_forced_tools:
        (
            effective_forced_tools,
            fallback_chain,
            unavailable_reason,
        ) = _resolve_explicit_forced_tools(
            service_port,
            effective_forced_tools=effective_forced_tools,
            canonical_tool_name=canonical_tool_name,
            canonical_tool_chain=canonical_tool_chain,
        )

    if (
        intent_category is not None
        and not effective_forced_tools
        and unavailable_reason is None
    ):
        effective_forced_tools, fallback_chain = _resolve_capability_tools(
            service_port,
            inbound=inbound,
            intent_category=intent_category,
            canonical_tool_chain=canonical_tool_chain,
        )
        if not effective_forced_tools and intent_category is not None:
            logger = service_port.logger
            if logger is not None:
                logger.info(
                    "No deterministic tool mapping for category '%s' after policy/filter checks; "
                    "continuing without forced tool.",
                    intent_category,
                )
        if effective_forced_tools:
            if intent_category == "browser":
                fallback_chain = service_port.augment_browser_fallback_chain(
                    fallback_chain=fallback_chain,
                )
            logger = service_port.logger
            if logger is not None:
                logger.info(
                    "Enforcing deterministic category '%s' with primary '%s' and fallback chain %s",
                    intent_category,
                    effective_forced_tools[0],
                    fallback_chain,
                )
            capability_primary = effective_forced_tools[0]

    if (
        explicit_capability
        and not effective_forced_tools
        and unavailable_reason is None
    ):
        unavailable_reason = "capability_tool_unavailable"

    if capability_primary is None and effective_forced_tools:
        capability_primary = effective_forced_tools[0]

    return ToolPlan(
        user_message=updated_user_message,
        intent_category=str(intent_category or NO_INTENT_CATEGORY),
        effective_forced_tools=effective_forced_tools,
        fallback_chain=fallback_chain,
        capability_primary=capability_primary,
        unavailable_reason=unavailable_reason,
        requested_forced_tools=requested_forced_tools,
    )
