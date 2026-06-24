from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .constants import (
    CHECKPOINT_ERROR_BUDGET_EXCEEDED,
    CHECKPOINT_ERROR_RANGE_INVALID,
    CHECKPOINT_ERROR_STABLE_ID_COLLISION,
)
from .schemas import (
    CheckpointFailedPayload,
    CheckpointStats,
    CheckpointStructuredState,
    CompressionBundle,
    CompressionCheckpoint,
    TierEntry,
    TierType,
)
from .token_count import count_tokens


@dataclass(frozen=True)
class DeltaEvent:
    """A single event from the session timeline to be consumed by strategies."""

    event_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    text: str | None = None
    refs: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TierStrategy(Protocol):
    """Protocol for extracting and compacting tier entries."""

    @property
    def tier_types(self) -> list[TierType]: ...

    def extract(
        self,
        events: list[DeltaEvent],
        current_bundle: CompressionBundle | None = None,
    ) -> list[TierEntry]: ...

    def abstract(
        self,
        existing_entries: list[TierEntry],
        new_entries: list[TierEntry],
        token_budget: int,
    ) -> list[TierEntry]: ...


def _trim_entries_to_budget(
    entries: list[TierEntry],
    token_budget: int,
    *,
    newest_first: bool = False,
    skip_oversize: bool = False,
) -> list[TierEntry]:
    used = 0
    trimmed: list[TierEntry] = []
    ordered = reversed(entries) if newest_first else entries
    for entry in ordered:
        if used + entry.token_count > token_budget:
            if skip_oversize:
                continue
            break
        if newest_first:
            trimmed.insert(0, entry)
        else:
            trimmed.append(entry)
        used += entry.token_count
    return trimmed


class SummaryStrategy:
    """Extracts and abstracts a rolling summary from dialogue events."""

    @property
    def tier_types(self) -> list[TierType]:
        return ["summary"]

    def extract(
        self,
        events: list[DeltaEvent],
        current_bundle: CompressionBundle | None = None,
    ) -> list[TierEntry]:
        dialogue_texts = [
            e.text
            for e in events
            if e.event_type in ("turn.user", "turn.assistant", "turn.completed")
            and e.text
        ]
        if not dialogue_texts:
            return []
        combined = " ".join(dialogue_texts)
        tokens = combined.split()
        truncated = " ".join(tokens[:400])
        return [
            TierEntry(
                tier_type="summary", text=truncated, token_count=len(tokens[:400])
            )
        ]

    def abstract(
        self,
        existing_entries: list[TierEntry],
        new_entries: list[TierEntry],
        token_budget: int,
    ) -> list[TierEntry]:
        all_text = " ".join(e.text for e in [*existing_entries, *new_entries] if e.text)
        tokens = all_text.split()
        truncated = " ".join(tokens[:token_budget])
        return [
            TierEntry(
                tier_type="summary",
                text=truncated,
                token_count=min(len(tokens), token_budget),
            )
        ]


class DecisionStrategy:
    """Extracts decision/constraint tiers from structured events."""

    @property
    def tier_types(self) -> list[TierType]:
        return ["decisions", "constraints"]

    def extract(
        self,
        events: list[DeltaEvent],
        current_bundle: CompressionBundle | None = None,
    ) -> list[TierEntry]:
        entries: list[TierEntry] = []
        for e in events:
            payload = e.payload
            if e.event_type == "decision.made" or "decision" in payload:
                text = (
                    payload.get("decision_text")
                    or payload.get("decision")
                    or e.text
                    or ""
                )
                if text:
                    entries.append(
                        TierEntry(
                            tier_type="decisions",
                            text=text,
                            refs=list(e.refs),
                            token_count=len(text.split()),
                        )
                    )
            if e.event_type == "constraint.set" or "constraint" in payload:
                text = payload.get("constraint_text") or payload.get("constraint") or ""
                if text:
                    entries.append(
                        TierEntry(
                            tier_type="constraints",
                            text=text,
                            refs=list(e.refs),
                            token_count=len(text.split()),
                        )
                    )
        return entries

    def abstract(
        self,
        existing_entries: list[TierEntry],
        new_entries: list[TierEntry],
        token_budget: int,
    ) -> list[TierEntry]:
        seen: dict[str, TierEntry] = {}
        for entry in [*existing_entries, *new_entries]:
            seen[entry.text.strip()] = entry
        return _trim_entries_to_budget(list(seen.values()), token_budget)


class EntityStrategy:
    """Extracts entity/topic tiers from events."""

    @property
    def tier_types(self) -> list[TierType]:
        return ["entities"]

    def extract(
        self,
        events: list[DeltaEvent],
        current_bundle: CompressionBundle | None = None,
    ) -> list[TierEntry]:
        entries: list[TierEntry] = []
        for e in events:
            entities = e.payload.get("entities") or []
            for ent in entities:
                text = ent if isinstance(ent, str) else str(ent)
                entries.append(
                    TierEntry(
                        tier_type="entities",
                        text=text,
                        refs=list(e.refs),
                        token_count=len(text.split()),
                    )
                )
        return entries

    def abstract(
        self,
        existing_entries: list[TierEntry],
        new_entries: list[TierEntry],
        token_budget: int,
    ) -> list[TierEntry]:
        seen: dict[str, TierEntry] = {}
        for entry in [*existing_entries, *new_entries]:
            seen[entry.text.strip().lower()] = entry
        return _trim_entries_to_budget(list(seen.values()), token_budget)


class ToolDigestStrategy:
    """Extracts tool-output digest tiers."""

    @property
    def tier_types(self) -> list[TierType]:
        return ["tool_digests"]

    def extract(
        self,
        events: list[DeltaEvent],
        current_bundle: CompressionBundle | None = None,
    ) -> list[TierEntry]:
        entries: list[TierEntry] = []
        for e in events:
            if e.event_type not in ("tool.completed", "tool.result"):
                continue
            tool_name = e.payload.get("tool_name", "unknown")
            summary = (
                e.payload.get("summary") or e.payload.get("distilled_summary") or ""
            )
            if not summary and e.text:
                words = e.text.split()[:100]
                summary = " ".join(words)
            if summary:
                entries.append(
                    TierEntry(
                        tier_type="tool_digests",
                        text=f"[{tool_name}] {summary}",
                        refs=list(e.refs),
                        meta={
                            "tool_name": tool_name,
                            "verified": e.payload.get("verified", False),
                        },
                        token_count=len(summary.split()) + 1,
                    )
                )
        return entries

    def abstract(
        self,
        existing_entries: list[TierEntry],
        new_entries: list[TierEntry],
        token_budget: int,
    ) -> list[TierEntry]:
        return _trim_entries_to_budget(
            [*existing_entries, *new_entries],
            token_budget,
            newest_first=True,
            skip_oversize=True,
        )


class StrategyRegistry:
    """Maps tier types to strategy implementations."""

    def __init__(self) -> None:
        self._strategies: dict[str, TierStrategy] = {}

    def register(self, strategy: TierStrategy) -> None:
        for tt in strategy.tier_types:
            self._strategies[tt] = strategy

    def get(self, tier_type: str) -> TierStrategy | None:
        return self._strategies.get(tier_type)

    def all_strategies(self) -> list[TierStrategy]:
        return list(dict.fromkeys(self._strategies.values()))

    @classmethod
    def default(cls) -> "StrategyRegistry":
        """Build a registry with all built-in strategies."""
        reg = cls()
        reg.register(SummaryStrategy())
        reg.register(DecisionStrategy())
        reg.register(EntityStrategy())
        reg.register(ToolDigestStrategy())
        reg.register(OpenLoopStrategy())
        return reg


class OpenLoopStrategy:
    """Extracts open-loop reminders and unresolved questions."""

    @property
    def tier_types(self) -> list[TierType]:
        return ["open_loops"]

    def extract(
        self,
        events: list[DeltaEvent],
        current_bundle: CompressionBundle | None = None,
    ) -> list[TierEntry]:
        entries: list[TierEntry] = []
        for e in events:
            payload = e.payload
            if (
                e.event_type in ("open_loop.created", "question.asked", "todo.created")
                or "open_loop" in payload
            ):
                text = (
                    payload.get("question_or_todo")
                    or payload.get("open_loop")
                    or payload.get("question")
                    or e.text
                    or ""
                )
                if text:
                    entries.append(
                        TierEntry(
                            tier_type="open_loops",
                            text=text,
                            refs=list(e.refs),
                            meta={
                                "owner": payload.get("owner"),
                                "status": payload.get("status", "open"),
                            },
                            token_count=len(text.split()),
                        )
                    )
        return entries

    def abstract(
        self,
        existing_entries: list[TierEntry],
        new_entries: list[TierEntry],
        token_budget: int,
    ) -> list[TierEntry]:
        seen: dict[str, TierEntry] = {}
        for entry in [*existing_entries, *new_entries]:
            status = entry.meta.get("status", "open")
            if status in ("closed", "resolved"):
                seen.pop(entry.text.strip(), None)
            else:
                seen[entry.text.strip()] = entry
        return _trim_entries_to_budget(list(seen.values()), token_budget)


@runtime_checkable
class DeltaSelector(Protocol):
    """Select the events included in the next checkpoint delta."""

    def select(
        self,
        all_events: list[DeltaEvent],
        last_checkpoint_to_event_id: str | None,
    ) -> list[DeltaEvent]: ...


@runtime_checkable
class CheckpointComposer(Protocol):
    """Assemble a checkpoint and fail closed on invariant violations."""

    def compose(
        self,
        session_id: str,
        checkpoint_id: str,
        created_at: str,
        from_event_id: str | None,
        to_event_id: str,
        summary_text: str,
        recent_window_event_ids: list[str],
        structured: CheckpointStructuredState,
        *,
        token_limit: int = 2400,
    ) -> "CompressionCheckpoint | CheckpointFailedPayload": ...


class AfterLastCheckpointSelector:
    """Default selector that keeps events after the last checkpoint boundary."""

    def select(
        self,
        all_events: list[DeltaEvent],
        last_checkpoint_to_event_id: str | None,
    ) -> list[DeltaEvent]:
        if last_checkpoint_to_event_id is None:
            return list(all_events)
        found = False
        selected: list[DeltaEvent] = []
        for e in all_events:
            if found:
                selected.append(e)
            elif e.event_id == last_checkpoint_to_event_id:
                found = True
        if not found:
            return list(all_events)
        return selected


class CheckpointComposerV1:
    """Default checkpoint composer with range, uniqueness, and budget checks."""

    def compose(
        self,
        session_id: str,
        checkpoint_id: str,
        created_at: str,
        from_event_id: str | None,
        to_event_id: str,
        summary_text: str,
        recent_window_event_ids: list[str],
        structured: CheckpointStructuredState,
        *,
        token_limit: int = 2400,
    ) -> "CompressionCheckpoint | CheckpointFailedPayload":
        if from_event_id is not None and from_event_id == to_event_id:
            return CheckpointFailedPayload(
                failure_id=str(uuid.uuid4()),
                session_id=session_id,
                reason="from_event_id equals to_event_id",
                error_code=CHECKPOINT_ERROR_RANGE_INVALID,
                created_at=created_at,
                from_event_id=from_event_id,
                until_event_id=to_event_id,
            )

        for items, label in [
            (structured.decisions, "decisions"),
            (structured.constraints, "constraints"),
            (structured.open_loops, "open_loops"),
        ]:
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                return CheckpointFailedPayload(
                    failure_id=str(uuid.uuid4()),
                    session_id=session_id,
                    reason=f"duplicate stable IDs in {label}",
                    error_code=CHECKPOINT_ERROR_STABLE_ID_COLLISION,
                    created_at=created_at,
                    from_event_id=from_event_id,
                    until_event_id=to_event_id,
                )

        summary_tokens = count_tokens(summary_text)
        structured_tokens = (
            sum(count_tokens(d.statement) for d in structured.decisions)
            + sum(count_tokens(c.statement) for c in structured.constraints)
            + sum(count_tokens(o.question_or_todo) for o in structured.open_loops)
            + sum(count_tokens(t.outcome) for t in structured.tool_digests)
        )
        total_tokens = summary_tokens + structured_tokens

        if total_tokens > token_limit:
            return CheckpointFailedPayload(
                failure_id=str(uuid.uuid4()),
                session_id=session_id,
                reason=f"token budget exceeded: {total_tokens} > {token_limit}",
                error_code=CHECKPOINT_ERROR_BUDGET_EXCEEDED,
                created_at=created_at,
                from_event_id=from_event_id,
                until_event_id=to_event_id,
                details={"total_tokens": total_tokens, "token_limit": token_limit},
            )

        stats = CheckpointStats(
            summary_tokens=summary_tokens,
            structured_tokens=structured_tokens,
            total_tokens=total_tokens,
        )

        return CompressionCheckpoint(
            checkpoint_id=checkpoint_id,
            session_id=session_id,
            created_at=created_at,
            from_event_id=from_event_id,
            to_event_id=to_event_id,
            summary_text=summary_text,
            recent_window_event_ids=list(recent_window_event_ids),
            structured=structured,
            stats=stats,
            version="1.6",
        )
