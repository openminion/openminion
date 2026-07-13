import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Any, Callable, List
from uuid import uuid4

from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.modules.context.budget import (
    ContextBudgetConfig,
    assemble_budgeted_context,
)
from openminion.modules.context.summary.engine import (
    DEFAULT_SESSION_SUMMARY_ENGINE,
    SessionSummaryEngine,
    SummaryTurn,
)
from openminion.modules.brain.constants import (
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
)
from openminion.modules.storage.runtime.pinned_context import (
    DEFAULT_PINNED_CONTEXT_POLICY,
    PinnedContextEntry,
    PinnedContextPolicy,
    render_pinned_context,
)
from openminion.modules.storage.runtime.session_store import MessageRecord, SessionStore
from openminion.services.bootstrap.paths import SERVICES_SESSION_CONTEXT_SUBDIR


@dataclass(frozen=True)
class SessionCompactionResult:
    session_id: str
    compacted_count: int
    compacted_until_rowid: int
    summary_updated: bool
    archive_relative_path: str = ""


@dataclass(frozen=True)
class SessionArchiveRef:
    path: str
    relative_path: str
    first_rowid: int
    last_rowid: int
    message_count: int
    first_created_at: str
    last_created_at: str


def _message_visible_to_summary(item: MessageRecord) -> bool:
    metadata = dict(getattr(item, "metadata", {}) or {})
    respond_kind = str(metadata.get("respond_kind", "") or "").strip()
    if respond_kind == RESPOND_KIND_POLICY_CONFIRMATION_PROMPT:
        return False
    role = str(getattr(item, "role", "") or "").strip()
    if role == "event":
        event_type = str(metadata.get("event_type", "") or "").strip()
        return event_type != SESSION_EVENT_POLICY_CONFIRMATION_PROMPT
    return True


def _summary_turns_from_messages(messages: list[MessageRecord]) -> list[SummaryTurn]:
    return [
        SummaryTurn(role=item.role, text=item.body)
        for item in messages
        if _message_visible_to_summary(item)
    ]


def resolve_session_archive_root(
    *,
    config: OpenMinionConfig,
    config_path: Path,
    storage_path: Path,
    memory_root: Path | None = None,
    data_root: Path | None = None,
) -> Path:
    configured = str(
        getattr(config.runtime, "session_archive_root_path", "") or ""
    ).strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    if data_root is not None:
        return (data_root / SERVICES_SESSION_CONTEXT_SUBDIR).resolve(strict=False)
    if memory_root is not None:
        return (memory_root / SERVICES_SESSION_CONTEXT_SUBDIR).resolve(strict=False)
    if config_path:
        return (config_path.parent / SERVICES_SESSION_CONTEXT_SUBDIR).resolve(
            strict=False
        )
    return (storage_path.parent / SERVICES_SESSION_CONTEXT_SUBDIR).resolve(strict=False)


class SessionContextService:
    def __init__(
        self,
        sessions: SessionStore,
        *,
        logger: logging.Logger | None = None,
        keep_recent_messages: int = 20,
        max_compact_per_turn: int = 100,
        summary_max_chars: int = 8000,
        archive_enabled: bool = True,
        archive_root: Path | None = None,
        archive_ref_limit: int = 3,
        token_budget: int = 0,
        chars_per_token: float = 4.0,
        retrieve_ctl: Any | None = None,
        summary_engine: SessionSummaryEngine | None = None,
        summary_enrichment_enabled: bool = False,
        summary_enricher: Callable[[str], str] | None = None,
        summary_enrichment_defer: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._sessions = sessions
        self._logger = logger or logging.getLogger(__name__)
        self._keep_recent_messages = max(1, int(keep_recent_messages))
        self._max_compact_per_turn = max(1, int(max_compact_per_turn))
        self._summary_max_chars = max(256, int(summary_max_chars))
        self._archive_enabled = bool(archive_enabled)
        self._archive_root = (
            archive_root.resolve() if archive_root is not None else None
        )
        self._archive_ref_limit = max(1, int(archive_ref_limit))
        self._token_budget = max(0, int(token_budget))
        self._chars_per_token = max(0.1, float(chars_per_token))
        self._retrieve_ctl = retrieve_ctl
        self._summary_engine = summary_engine or DEFAULT_SESSION_SUMMARY_ENGINE
        self._summary_enrichment_enabled = bool(summary_enrichment_enabled)
        self._summary_enricher = summary_enricher
        self._summary_enrichment_defer = (
            summary_enrichment_defer or self._default_defer_summary_task
        )
        self._session_close_callbacks: list[Callable[[str], None]] = []
        if self._archive_enabled and self._archive_root is not None:
            self._archive_root.mkdir(parents=True, exist_ok=True)

    def register_close_callback(self, callback: Callable[[str], None]) -> None:
        self._session_close_callbacks.append(callback)

    def ensure_session_context(self, *, session_id: str) -> object:
        return self._sessions.ensure_session_context(session_id=session_id)

    def get_turn_count(self, *, session_id: str) -> int:
        return self._sessions.count_messages(session_id=session_id)

    def estimate_token_pressure(self, *, session_id: str) -> tuple[int, int, float]:
        """Return estimated session token pressure against this service budget."""
        token_budget = max(0, int(self._token_budget))
        if token_budget <= 0:
            return 0, 0, 0.0
        total_messages = self._sessions.count_messages(session_id=session_id)
        if total_messages <= 0:
            return 0, token_budget, 0.0
        messages = self._sessions.list_recent_messages(
            session_id=session_id,
            limit=total_messages,
        )
        total_chars = sum(
            len(str(getattr(item, "body", "") or "")) for item in messages
        )
        token_estimate = int(total_chars / max(0.1, float(self._chars_per_token)))
        pressure = min(1.0, token_estimate / float(token_budget))
        return token_estimate, token_budget, pressure

    def build_summary_checkpoint(
        self,
        *,
        session_id: str,
    ) -> tuple[str, int]:
        context = self._sessions.ensure_session_context(session_id=session_id)
        rolling_summary = str(getattr(context, "rolling_summary", "") or "").strip()
        total_messages = self._sessions.count_messages(session_id=session_id)
        if total_messages <= 0:
            return rolling_summary, int(
                getattr(context, "compacted_message_count", 0) or 0
            )
        uncompacted_count = max(0, total_messages - context.compacted_message_count)
        if uncompacted_count <= 0:
            logical_total = max(
                total_messages,
                int(getattr(context, "compacted_message_count", 0) or 0),
            )
            return rolling_summary, logical_total

        remaining = self._sessions.list_recent_messages(
            session_id=session_id,
            limit=uncompacted_count,
        )
        if not remaining:
            logical_total = max(
                total_messages,
                int(getattr(context, "compacted_message_count", 0) or 0),
            )
            return rolling_summary, logical_total

        summary_turns = _summary_turns_from_messages(remaining)
        summary_delta = self._summary_engine.summarize_compaction_chunk(
            summary_turns
        ).summary_text
        merged_summary = self._summary_engine.merge_summary(
            current=rolling_summary,
            delta=summary_delta,
            max_chars=self._summary_max_chars,
        )
        logical_total = max(
            total_messages,
            int(getattr(context, "compacted_message_count", 0) or 0) + len(remaining),
        )
        return merged_summary, logical_total

    def list_recent_messages(
        self,
        *,
        session_id: str,
        limit: int = 20,
    ) -> list[MessageRecord]:
        return self._sessions.list_recent_messages(
            session_id=session_id,
            limit=limit,
        )

    def on_session_close(self, *, session_id: str) -> SessionCompactionResult:
        result = self.compact_session(session_id=session_id)
        context = self._sessions.ensure_session_context(session_id=session_id)
        if not str(context.rolling_summary or "").strip():
            total_messages = self._sessions.count_messages(session_id=session_id)
            if total_messages > 0:
                remaining = self._sessions.list_messages_after_rowid(
                    session_id=session_id,
                    after_rowid=context.compacted_until_rowid,
                    limit=total_messages,
                )
                if remaining:
                    summary_turns = _summary_turns_from_messages(remaining)
                    merged_summary = self._summary_engine.merge_summary(
                        current="",
                        delta=self._summary_engine.summarize_compaction_chunk(
                            summary_turns
                        ).summary_text,
                        max_chars=self._summary_max_chars,
                    )
                    last_message = remaining[-1]
                    updated_context = self._sessions.update_session_context(
                        session_id=session_id,
                        summary_short=_summary_short_from_rolling_summary(
                            merged_summary
                        ),
                        rolling_summary=merged_summary,
                        compacted_until_rowid=last_message.rowid,
                        compacted_until_created_at=last_message.created_at,
                        compacted_until_message_id=last_message.id,
                        compacted_message_count=total_messages,
                        version=context.version + 1,
                        expected_version=context.version,
                    )
                    context = updated_context
                    result = SessionCompactionResult(
                        session_id=session_id,
                        compacted_count=total_messages,
                        compacted_until_rowid=last_message.rowid,
                        summary_updated=True,
                        archive_relative_path=result.archive_relative_path,
                    )
        for callback in list(self._session_close_callbacks):
            try:
                callback(session_id)
            except Exception as exc:
                self._logger.warning(
                    "session close callback failed session_id=%s error=%s",
                    session_id,
                    exc,
                )
        return result

    def compact_session(self, *, session_id: str) -> SessionCompactionResult:
        context = self._sessions.ensure_session_context(session_id=session_id)
        total_messages = self._sessions.count_messages(session_id=session_id)
        to_compact = self._messages_to_compact(
            session_id=session_id,
            context=context,
            total_messages=total_messages,
        )
        if not to_compact:
            return SessionCompactionResult(
                session_id=session_id,
                compacted_count=0,
                compacted_until_rowid=context.compacted_until_rowid,
                summary_updated=False,
                archive_relative_path="",
            )
        archive_ref = self._archive_compacted_messages(
            session_id=session_id,
            messages=to_compact,
        )
        self._ingest_compacted_messages(
            session_id=session_id,
            messages=to_compact,
        )
        merged_summary = self._merged_compaction_summary(
            context=context,
            to_compact=to_compact,
        )
        last_message = to_compact[-1]
        updated_context = self._update_compacted_context(
            session_id=session_id,
            context=context,
            to_compact=to_compact,
            merged_summary=merged_summary,
        )
        if updated_context.version == context.version:
            return self._compaction_conflict_result(
                session_id,
                context_version=context.version,
                updated_context=updated_context,
            )
        self._maybe_schedule_summary_enrichment(
            session_id=session_id,
            deterministic_summary=merged_summary,
        )
        self._record_compaction_archive_event(
            session_id,
            archive_ref=archive_ref,
        )
        self._log_compaction_result(session_id=session_id, to_compact=to_compact)
        return SessionCompactionResult(
            session_id=session_id,
            compacted_count=len(to_compact),
            compacted_until_rowid=last_message.rowid,
            summary_updated=True,
            archive_relative_path=archive_ref.relative_path
            if archive_ref is not None
            else "",
        )

    def _messages_to_compact(
        self,
        *,
        session_id: str,
        context: Any,
        total_messages: int,
    ) -> list[MessageRecord]:
        uncompacted_count = max(0, total_messages - context.compacted_message_count)
        if uncompacted_count <= self._keep_recent_messages:
            return []
        target_compact_count = min(
            self._max_compact_per_turn,
            uncompacted_count - self._keep_recent_messages,
        )
        return self._sessions.list_messages_after_rowid(
            session_id=session_id,
            after_rowid=context.compacted_until_rowid,
            limit=target_compact_count,
        )

    def _merged_compaction_summary(
        self,
        *,
        context: Any,
        to_compact: list[MessageRecord],
    ) -> str:
        summary_turns = [
            SummaryTurn(role=item.role, text=item.body) for item in to_compact
        ]
        summary_delta = self._summary_engine.summarize_compaction_chunk(
            summary_turns
        ).summary_text
        return self._summary_engine.merge_summary(
            current=context.rolling_summary,
            delta=summary_delta,
            max_chars=self._summary_max_chars,
        )

    def _update_compacted_context(
        self,
        *,
        session_id: str,
        context: Any,
        to_compact: list[MessageRecord],
        merged_summary: str,
    ) -> Any:
        last_message = to_compact[-1]
        return self._sessions.update_session_context(
            session_id=session_id,
            summary_short=_summary_short_from_rolling_summary(merged_summary),
            rolling_summary=merged_summary,
            compacted_until_rowid=last_message.rowid,
            compacted_until_created_at=last_message.created_at,
            compacted_until_message_id=last_message.id,
            compacted_message_count=context.compacted_message_count + len(to_compact),
            version=context.version + 1,
            expected_version=context.version,
        )

    def _compaction_conflict_result(
        self,
        session_id: str,
        *,
        context_version: int,
        updated_context: Any,
    ) -> SessionCompactionResult:
        self._logger.warning(
            "session compaction conflict: session_id=%s expected_version=%s "
            "current_version=%s — concurrent compaction detected, skipping",
            session_id,
            context_version,
            updated_context.version,
        )
        return SessionCompactionResult(
            session_id=session_id,
            compacted_count=0,
            compacted_until_rowid=updated_context.compacted_until_rowid,
            summary_updated=False,
            archive_relative_path="",
        )

    def _record_compaction_archive_event(
        self,
        session_id: str,
        *,
        archive_ref: SessionArchiveRef | None,
    ) -> None:
        if archive_ref is None:
            return
        try:
            self._sessions.append_event(
                session_id=session_id,
                event_type="session.compaction.archive",
                payload=_archive_ref_to_payload(archive_ref),
            )
        except Exception as exc:
            self._logger.warning(
                "session archive event write failed session_id=%s error=%s",
                session_id,
                exc,
            )

    def _log_compaction_result(
        self,
        *,
        session_id: str,
        to_compact: list[MessageRecord],
    ) -> None:
        self._logger.debug(
            "session context compacted session_id=%s compacted_count=%s compacted_until_rowid=%s",
            session_id,
            len(to_compact),
            to_compact[-1].rowid,
        )

    def _ingest_compacted_messages(
        self,
        *,
        session_id: str,
        messages: list[MessageRecord],
    ) -> None:
        """Ingest compacted messages into RetrieveCtl as episode units."""
        if self._retrieve_ctl is None:
            return

        def _role_of(msg: MessageRecord) -> str:
            r = str(msg.role or "").strip().lower()
            if r in {"inbound", "user"}:
                return "user"
            if r in {"outbound", "assistant"}:
                return "assistant"
            return r

        i = 0
        while i < len(messages):
            msg = messages[i]
            role = _role_of(msg)
            # Try to form a user→assistant pair
            if (
                role == "user"
                and i + 1 < len(messages)
                and _role_of(messages[i + 1]) == "assistant"
            ):
                pair = messages[i + 1]
                source_ref = f"session:{session_id}#rowid:{msg.rowid}-{pair.rowid}"
                text = f"user: {msg.body}\nassistant: {pair.body}"
                created_at = msg.created_at
                try:
                    self._retrieve_ctl.ingest_source(
                        source_type="episode",
                        source_ref=source_ref,
                        text=text,
                        scope=f"session:{session_id}",
                        tags=["session", "compact", "turn-pair"],
                        created_at=created_at,
                    )
                except Exception as exc:
                    self._logger.warning(
                        "session episode ingest failed session_id=%s rowid=%s-%s error=%s",
                        session_id,
                        msg.rowid,
                        pair.rowid,
                        exc,
                    )
                i += 2
            else:
                # Ingest individually (system messages, consecutive same-role, etc.)
                source_ref = f"session:{session_id}#rowid:{msg.rowid}"
                text = f"{role}: {msg.body}"
                try:
                    self._retrieve_ctl.ingest_source(
                        source_type="episode",
                        source_ref=source_ref,
                        text=text,
                        scope=f"session:{session_id}",
                        tags=["session", "compact"],
                        created_at=msg.created_at,
                    )
                except Exception as exc:
                    self._logger.warning(
                        "session episode ingest failed session_id=%s rowid=%s error=%s",
                        session_id,
                        msg.rowid,
                        exc,
                    )
                i += 1

    def build_history(
        self,
        *,
        session_id: str,
        channel: str,
        target: str,
        recent_limit: int,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> List[Message]:
        conversation_value = str(conversation_id or "").strip()
        thread_value = str(thread_id or "").strip()
        context = (
            self._sessions.ensure_session_context(session_id=session_id)
            if not conversation_value
            else None
        )

        system_messages: list[Message] = []
        if context is not None:
            archive_refs = self._list_recent_archive_refs(session_id=session_id)
            rendered_context = _render_context_block(
                context.pinned_context,
                context.rolling_summary,
                archive_refs=archive_refs,
            )
            if rendered_context:
                system_messages.append(
                    Message(
                        channel=channel,
                        target=target,
                        body=rendered_context,
                        metadata={
                            "role": "system",
                            "session_id": session_id,
                            "context_compacted": "true",
                            "context_archive_ref_count": str(len(archive_refs)),
                        },
                    )
                )

        recent_records = self._sessions.list_recent_messages(
            session_id=session_id,
            limit=max(1, int(recent_limit)),
            conversation_id=conversation_value or None,
            thread_id=thread_value or None,
        )
        history_messages = [
            Message(
                channel=channel,
                target=target,
                body=item.body,
                metadata={
                    "role": item.role,
                    "session_id": session_id,
                    **(
                        {"conversation_id": conversation_value}
                        if conversation_value
                        else {}
                    ),
                    **({"thread_id": thread_value} if thread_value else {}),
                },
                id=item.id,
            )
            for item in recent_records
        ]

        if self._token_budget <= 0:
            return system_messages + history_messages

        budget_config = ContextBudgetConfig(
            max_tokens=self._token_budget,
            chars_per_token=self._chars_per_token,
        )
        budgeted = assemble_budgeted_context(
            system_messages=system_messages,
            history_messages=history_messages,
            budget=budget_config,
        )

        try:
            self._sessions.append_event(
                session_id=session_id,
                event_type="session.context.budget",
                payload=budgeted.telemetry.to_dict(),
            )
        except Exception as exc:
            self._logger.warning("session budget telemetry write failed: %s", exc)

        return budgeted.messages

    async def acompact_session(self, *, session_id: str) -> SessionCompactionResult:
        return await asyncio.to_thread(self.compact_session, session_id=session_id)

    async def abuild_history(
        self,
        *,
        session_id: str,
        channel: str,
        target: str,
        recent_limit: int,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> List[Message]:
        return await asyncio.to_thread(
            self.build_history,
            session_id=session_id,
            channel=channel,
            target=target,
            recent_limit=recent_limit,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )

    def _maybe_schedule_summary_enrichment(
        self, *, session_id: str, deterministic_summary: str
    ) -> None:
        if not self._summary_enrichment_enabled:
            return
        if self._summary_enricher is None:
            return
        summary_enricher = self._summary_enricher
        base_summary = str(deterministic_summary or "").strip()
        if not base_summary:
            return

        def _task() -> None:
            try:
                enriched = str(summary_enricher(base_summary) or "").strip()
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "session summary enrichment failed session_id=%s error=%s",
                    session_id,
                    exc,
                )
                return
            if not enriched or enriched == base_summary:
                return
            safe_summary = enriched[-self._summary_max_chars :]
            try:
                context = self._sessions.ensure_session_context(session_id=session_id)
                if context.rolling_summary.strip() != base_summary:
                    return
                self._sessions.update_session_context(
                    session_id=session_id,
                    summary_short=_summary_short_from_rolling_summary(safe_summary),
                    rolling_summary=safe_summary,
                    compacted_until_rowid=context.compacted_until_rowid,
                    compacted_until_created_at=context.compacted_until_created_at,
                    compacted_until_message_id=context.compacted_until_message_id,
                    compacted_message_count=context.compacted_message_count,
                    version=context.version + 1,
                    expected_version=context.version,
                )
                self._sessions.append_event(
                    session_id=session_id,
                    event_type="session.summary.enriched",
                    payload={"mode": "deferred", "chars": len(safe_summary)},
                )
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "session summary enrichment apply failed session_id=%s error=%s",
                    session_id,
                    exc,
                )

        try:
            self._summary_enrichment_defer(_task)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "session summary enrichment scheduling failed session_id=%s error=%s",
                session_id,
                exc,
            )

    def list_pins(self, *, session_id: str) -> list[PinnedContextEntry]:
        return self._sessions.list_pins(session_id=session_id)

    def add_pin(
        self,
        *,
        session_id: str,
        source: str,
        text: str,
        pin_id: str | None = None,
        created_at: str | None = None,
        policy: PinnedContextPolicy = DEFAULT_PINNED_CONTEXT_POLICY,
    ) -> list[PinnedContextEntry]:
        return self._sessions.add_pin(
            session_id=session_id,
            source=source,
            text=text,
            pin_id=pin_id,
            created_at=created_at,
            policy=policy,
        )

    def remove_pin(
        self,
        *,
        session_id: str,
        pin_id: str | None = None,
        text: str | None = None,
        source: str | None = None,
        policy: PinnedContextPolicy = DEFAULT_PINNED_CONTEXT_POLICY,
    ) -> list[PinnedContextEntry]:
        return self._sessions.remove_pin(
            session_id=session_id,
            pin_id=pin_id,
            text=text,
            source=source,
            policy=policy,
        )

    @staticmethod
    def _default_defer_summary_task(task: Callable[[], None]) -> None:
        thread = Thread(target=task, daemon=True)
        thread.start()

    def _archive_compacted_messages(
        self,
        *,
        session_id: str,
        messages: List[MessageRecord],
    ) -> SessionArchiveRef | None:
        if not self._archive_enabled or self._archive_root is None:
            return None
        if not messages:
            return None

        first = messages[0]
        last = messages[-1]
        now = datetime.now(timezone.utc)
        partition = now.strftime("%Y-%m-%d")
        session_dir = self._archive_root / _slug(session_id) / partition
        session_dir.mkdir(parents=True, exist_ok=True)
        file_name = (
            f"chunk-{first.rowid:010d}-{last.rowid:010d}-"
            f"{now.strftime('%H%M%S')}-{uuid4().hex[:8]}.jsonl"
        )
        file_path = session_dir / file_name

        with file_path.open("w", encoding="utf-8") as handle:
            for item in messages:
                payload = {
                    "rowid": int(item.rowid),
                    "id": item.id,
                    "session_id": item.session_id,
                    "role": item.role,
                    "body": item.body,
                    "metadata": item.metadata,
                    "created_at": item.created_at,
                }
                handle.write(json.dumps(payload, sort_keys=True) + "\n")

        try:
            relative_path = str(file_path.relative_to(self._archive_root))
        except ValueError:
            relative_path = str(file_path)

        return SessionArchiveRef(
            path=str(file_path),
            relative_path=relative_path,
            first_rowid=first.rowid,
            last_rowid=last.rowid,
            message_count=len(messages),
            first_created_at=first.created_at,
            last_created_at=last.created_at,
        )

    def _list_recent_archive_refs(self, *, session_id: str) -> list[dict[str, Any]]:
        events = self._sessions.list_events(
            session_id=session_id,
            limit=self._archive_ref_limit,
            newest_first=True,
            event_type_prefix="session.compaction.archive",
        )
        refs: list[dict[str, Any]] = []
        for event in reversed(events):
            payload = event.payload if isinstance(event.payload, dict) else {}
            ref_path = str(
                payload.get("relative_path") or payload.get("path") or ""
            ).strip()
            if not ref_path:
                continue
            refs.append(
                {
                    "path": ref_path,
                    "first_rowid": _safe_int(payload.get("first_rowid"), default=0),
                    "last_rowid": _safe_int(payload.get("last_rowid"), default=0),
                    "message_count": _safe_int(payload.get("message_count"), default=0),
                }
            )
        return refs


def _render_context_block(
    pinned_context: str,
    rolling_summary: str,
    *,
    archive_refs: list[dict[str, Any]] | None = None,
) -> str:
    pinned = render_pinned_context(pinned_context)
    summary = rolling_summary.strip()
    refs = list(archive_refs or [])
    if not pinned and not summary and not refs:
        return ""

    sections: list[str] = [
        "Session context (compacted). Use this as continuity reference.",
    ]
    if pinned:
        sections.append("Pinned context:\n" + pinned)
    if summary:
        sections.append("Rolling summary:\n" + summary)
    if refs:
        ref_lines: list[str] = []
        for ref in refs:
            path = str(ref.get("path", "")).strip()
            first_rowid = _safe_int(ref.get("first_rowid"), default=0)
            last_rowid = _safe_int(ref.get("last_rowid"), default=0)
            message_count = _safe_int(ref.get("message_count"), default=0)
            if not path:
                continue
            ref_lines.append(
                f"- {path} (rowid={first_rowid}-{last_rowid}, messages={message_count})"
            )
        if ref_lines:
            sections.append(
                "Compaction archive refs (full transcript chunks):\n"
                + "\n".join(ref_lines)
            )
    return "\n\n".join(sections).strip()


def _archive_ref_to_payload(archive_ref: SessionArchiveRef) -> dict[str, Any]:
    return {
        "path": archive_ref.path,
        "relative_path": archive_ref.relative_path,
        "first_rowid": archive_ref.first_rowid,
        "last_rowid": archive_ref.last_rowid,
        "message_count": archive_ref.message_count,
        "first_created_at": archive_ref.first_created_at,
        "last_created_at": archive_ref.last_created_at,
    }


def _summary_short_from_rolling_summary(rolling_summary: str) -> str:
    summary = str(rolling_summary or "").strip()
    if not summary:
        return ""
    first_line = summary.splitlines()[0].strip()
    if not first_line:
        return ""
    return first_line[:240]


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").strip().lower()).strip(
        "-"
    )
    return normalized or "session"
