from dataclasses import dataclass
from openminion.base.types import Message

_COMPACTED_MESSAGE_PREFIX = "[context budget compacted message:"
_COMPACTED_OMISSION_PREFIX = "[... omitted "


@dataclass(frozen=True)
class ContextBudgetConfig:
    """Control knobs for token-budgeted context assembly."""

    max_tokens: int = 0
    chars_per_token: float = 4.0
    min_recent_messages: int = 4


@dataclass
class ContextBudgetTelemetry:
    """Budget usage telemetry emitted per turn for observability."""

    estimated_tokens_total: int = 0
    estimated_tokens_system: int = 0
    estimated_tokens_history: int = 0
    messages_before_trim: int = 0
    messages_after_trim: int = 0
    trimmed_count: int = 0
    budget_used_pct: float = 0.0
    overflow: bool = False
    budget_chars: int = 0
    max_tokens: int = 0

    def to_dict(self) -> dict[str, int | float | bool]:
        return {
            "estimated_tokens_total": self.estimated_tokens_total,
            "estimated_tokens_system": self.estimated_tokens_system,
            "estimated_tokens_history": self.estimated_tokens_history,
            "messages_before_trim": self.messages_before_trim,
            "messages_after_trim": self.messages_after_trim,
            "trimmed_count": self.trimmed_count,
            "budget_used_pct": round(self.budget_used_pct, 2),
            "overflow": self.overflow,
            "budget_chars": self.budget_chars,
            "max_tokens": self.max_tokens,
        }


@dataclass
class BudgetedContext:
    messages: list[Message]
    telemetry: ContextBudgetTelemetry


def assemble_budgeted_context(
    *,
    system_messages: list[Message],
    history_messages: list[Message],
    budget: ContextBudgetConfig,
) -> BudgetedContext:
    is_unlimited = budget.max_tokens <= 0
    budget_chars = (
        int(budget.max_tokens * budget.chars_per_token) if not is_unlimited else 0
    )

    system_chars = sum(_msg_chars(m) for m in system_messages)
    history_chars = sum(_msg_chars(m) for m in history_messages)
    total_chars = system_chars + history_chars

    telemetry = ContextBudgetTelemetry(
        estimated_tokens_total=_chars_to_tokens(total_chars, budget.chars_per_token),
        estimated_tokens_system=_chars_to_tokens(system_chars, budget.chars_per_token),
        estimated_tokens_history=_chars_to_tokens(
            history_chars, budget.chars_per_token
        ),
        messages_before_trim=len(history_messages),
        messages_after_trim=len(history_messages),
        trimmed_count=0,
        budget_used_pct=0.0,
        overflow=False,
        budget_chars=budget_chars,
        max_tokens=budget.max_tokens,
    )

    if is_unlimited or total_chars <= budget_chars:
        if not is_unlimited and budget_chars > 0:
            telemetry.budget_used_pct = round(total_chars / budget_chars * 100, 2)
        return BudgetedContext(
            messages=list(system_messages) + list(history_messages),
            telemetry=telemetry,
        )

    min_recent = max(0, int(budget.min_recent_messages))
    trimable = (
        list(history_messages[:-min_recent])
        if min_recent < len(history_messages)
        else []
    )
    protected = (
        list(history_messages[-min_recent:])
        if min_recent > 0
        else list(history_messages)
    )

    trimmed: list[Message] = []
    remaining = list(trimable)
    while remaining:
        candidate_history = remaining + protected
        candidate_chars = system_chars + sum(_msg_chars(m) for m in candidate_history)
        if candidate_chars <= budget_chars:
            break
        trimmed.append(remaining.pop(0))

    trimmed_history = remaining + protected
    total_final_chars = system_chars + sum(_msg_chars(m) for m in trimmed_history)
    if total_final_chars > budget_chars:
        trimmed_history = _fit_recent_history_to_budget(
            system_chars=system_chars,
            history_messages=trimmed_history,
            budget_chars=budget_chars,
        )
        total_final_chars = system_chars + sum(_msg_chars(m) for m in trimmed_history)
    overflow = total_final_chars > budget_chars

    telemetry.messages_after_trim = len(trimmed_history)
    telemetry.trimmed_count = len(trimmed)
    telemetry.estimated_tokens_total = _chars_to_tokens(
        total_final_chars, budget.chars_per_token
    )
    telemetry.estimated_tokens_history = _chars_to_tokens(
        sum(_msg_chars(m) for m in trimmed_history), budget.chars_per_token
    )
    if budget_chars > 0:
        telemetry.budget_used_pct = round(total_final_chars / budget_chars * 100, 2)
    telemetry.overflow = overflow

    return BudgetedContext(
        messages=list(system_messages) + trimmed_history,
        telemetry=telemetry,
    )


def _msg_chars(msg: Message) -> int:
    body = str(msg.body or "")
    meta_str = str(msg.metadata or "")
    return len(body) + len(meta_str)


def _fit_recent_history_to_budget(
    *,
    system_chars: int,
    history_messages: list[Message],
    budget_chars: int,
) -> list[Message]:
    available_history_chars = max(0, int(budget_chars) - max(0, int(system_chars)))
    if available_history_chars <= 0:
        return []

    selected_newest_first: list[Message] = []
    used_chars = 0
    for message in reversed(history_messages):
        remaining_chars = available_history_chars - used_chars
        if remaining_chars <= 0:
            break
        message_chars = _msg_chars(message)
        if message_chars <= remaining_chars:
            selected_newest_first.append(message)
            used_chars += message_chars
            continue
        compacted = _compact_message_to_char_limit(message, remaining_chars)
        if compacted is not None:
            selected_newest_first.append(compacted)
        break
    return list(reversed(selected_newest_first))


def _compact_message_to_char_limit(message: Message, max_chars: int) -> Message | None:
    meta_chars = len(str(message.metadata or ""))
    body_limit = max(0, int(max_chars) - meta_chars)
    if body_limit <= 0:
        return None
    compacted_body = _compact_text_to_limit(str(message.body or ""), body_limit)
    if not compacted_body:
        return None
    return Message(
        channel=message.channel,
        target=message.target,
        body=compacted_body,
        metadata=dict(message.metadata or {}),
        stats=message.stats,
        id=message.id,
        timestamp=message.timestamp,
    )


def _compact_text_to_limit(text: str, max_chars: int) -> str:
    limit = max(0, int(max_chars))
    if limit <= 0:
        return ""
    body = str(text or "")
    if len(body) <= limit:
        return body

    note = f"{_COMPACTED_MESSAGE_PREFIX} original_chars={len(body)}]\n"
    if limit <= len(note):
        return note[:limit].rstrip()

    omission = f"\n{_COMPACTED_OMISSION_PREFIX}{len(body)} chars ...]\n"
    payload_limit = limit - len(note) - len(omission)
    if payload_limit <= 0:
        return (note + omission.lstrip())[:limit].rstrip()

    head_chars = max(1, (payload_limit + 1) // 2)
    tail_chars = max(0, payload_limit - head_chars)
    omitted = max(0, len(body) - head_chars - tail_chars)
    omission = f"\n{_COMPACTED_OMISSION_PREFIX}{omitted} chars ...]\n"
    tail = body[-tail_chars:] if tail_chars > 0 else ""
    compacted = note + body[:head_chars].rstrip() + omission + tail.lstrip()
    return compacted[:limit].rstrip()


def _chars_to_tokens(chars: int, chars_per_token: float) -> int:
    if chars_per_token <= 0:
        return 0
    return max(0, int(chars / chars_per_token))
