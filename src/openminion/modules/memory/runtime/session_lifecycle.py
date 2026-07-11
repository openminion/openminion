import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from openminion.modules.memory.runtime.gc import (
    apply_confidence_decay,
    compress_old_summaries as compress_old_summaries_gc,
    enforce_scope_capacity,
    evict_stale_insights,
    purge_soft_deleted,
)
from openminion.modules.memory.models import MemoryCandidate, SessionSummaryContent
from openminion.modules.memory.runtime.extraction.records import _content_text
from openminion.modules.prompting.memory import (
    CURRENT_SESSION_CALLBACK_CONTEXT_LABEL,
    CURRENT_SESSION_SUMMARY_HEADER,
    PRIOR_SESSION_CONTEXT_LABEL,
    PRIOR_SESSION_SUMMARY_HEADER,
)


class SessionLifecycleMixin:
    _PLAN_SNAPSHOT_PENDING_INTENT_STATUSES = frozenset(
        {"pending", "in_progress", "retrying", "blocked", "needs_user"}
    )
    _PLAN_SNAPSHOT_ALLOWED_REASONS = frozenset({"budget_exhausted", "iteration_cap"})
    _SESSION_SUMMARY_STRUCTURER_MIN_TURN_COUNT = 3
    _SESSION_SUMMARY_STRUCTURER_SHORT_SUMMARY_MAX_CHARS = 256

    def _long_term_scopes(self) -> list[str]:
        scopes = [f"agent:{self._agent_id}"]
        if self._project_id:
            scopes.append(f"project:{self._project_id}")
        scopes.append("global:system")
        return scopes

    def _latest_working_state_inline(self, *, session_id: str) -> dict[str, Any] | None:
        getter = getattr(self, "_working_state_getter", None)
        if callable(getter):
            try:
                raw = getter(session_id)
            except Exception:
                raw = None
        else:
            raw = None
        if not isinstance(raw, dict):
            return None
        state_inline = (
            raw.get("state_inline")
            if isinstance(raw.get("state_inline"), dict)
            else raw
        )
        return dict(state_inline) if isinstance(state_inline, dict) else None

    def _latest_runtime_turn_metadata(self, *, session_id: str) -> dict[str, Any]:
        session_context = getattr(self, "_session_context", None)
        if session_context is None:
            return {}
        list_recent = getattr(session_context, "list_recent_messages", None)
        if not callable(list_recent):
            return {}
        try:
            messages = list_recent(session_id=session_id, limit=8)
        except Exception:
            return {}
        for item in list(messages or []):
            role = str(getattr(item, "role", "") or "").strip().lower()
            if role != "outbound":
                continue
            metadata = getattr(item, "metadata", None)
            if isinstance(metadata, dict):
                return dict(metadata)
        return {}

    def _plan_snapshot_incomplete_reason(
        self,
        *,
        brain_status: str,
        termination_reason: str,
        has_incomplete_intents: bool,
    ) -> str:
        normalized_reason = str(termination_reason or "").strip().lower()
        if normalized_reason in self._PLAN_SNAPSHOT_ALLOWED_REASONS:
            return normalized_reason
        normalized_status = str(brain_status or "").strip().lower()
        if normalized_status == "done" and has_incomplete_intents:
            return "session_ended"
        return "session_ended"

    def _plan_snapshot_content(
        self,
        *,
        session_id: str,
        state_inline: dict[str, Any],
        turn_index: int,
        brain_status: str,
        termination_reason: str,
    ) -> dict[str, Any] | None:
        plan_payload = state_inline.get("plan")
        raw_steps = (
            list(plan_payload.get("steps", []))
            if isinstance(plan_payload, dict)
            and isinstance(plan_payload.get("steps"), list)
            else []
        )
        raw_intents = (
            list(state_inline.get("intent_execution_states", []) or [])
            if isinstance(state_inline.get("intent_execution_states"), list)
            else []
        )
        incomplete_intents = [
            item
            for item in raw_intents
            if isinstance(item, dict)
            and str(item.get("status", "") or "").strip().lower()
            in self._PLAN_SNAPSHOT_PENDING_INTENT_STATUSES
        ]
        if not raw_steps and not incomplete_intents:
            return None

        try:
            cursor = max(0, int(state_inline.get("cursor", 0) or 0))
        except Exception:
            cursor = 0

        plan_steps: list[dict[str, Any]] = []
        for index, step in enumerate(raw_steps):
            if not isinstance(step, dict):
                continue
            step_id = str(
                step.get("command_id") or step.get("id") or f"step-{index + 1}"
            ).strip()
            if not step_id:
                continue
            if index < cursor:
                step_status = "succeeded"
            elif index == cursor:
                step_status = "in_progress"
            else:
                step_status = "pending"
            plan_steps.append({"step_id": step_id, "status": step_status})

        intent_states: list[dict[str, Any]] = []
        for item in raw_intents:
            if not isinstance(item, dict):
                continue
            intent_id = str(item.get("intent_id", "") or "").strip()
            status = str(item.get("status", "") or "").strip().lower()
            if not intent_id or not status:
                continue
            intent_states.append({"intent_id": intent_id, "status": status})

        last_work_summary = str(
            state_inline.get("session_work_summary", "") or ""
        ).strip()
        incomplete_reason = self._plan_snapshot_incomplete_reason(
            brain_status=brain_status,
            termination_reason=termination_reason,
            has_incomplete_intents=bool(incomplete_intents),
        )
        payload: dict[str, Any] = {
            "plan_steps": plan_steps,
            "intent_states": intent_states,
            "last_work_summary": last_work_summary,
            "incomplete_reason": incomplete_reason,
            "session_id": session_id,
            "turn_index": max(0, int(turn_index or 0)),
        }
        payload["text"] = json.dumps(
            {
                "incomplete_reason": incomplete_reason,
                "intent_states": intent_states,
                "last_work_summary": last_work_summary,
                "plan_steps": plan_steps,
                "session_id": session_id,
                "turn_index": max(0, int(turn_index or 0)),
            },
            sort_keys=True,
        )
        return payload

    def write_plan_snapshot(self, session_id: str) -> str | None:
        session_context = getattr(self, "_session_context", None)
        if session_context is None:
            return None
        state_inline = self._latest_working_state_inline(session_id=session_id)
        if state_inline is None:
            return None
        metadata = self._latest_runtime_turn_metadata(session_id=session_id)
        turn_index = 0
        get_turn_count = getattr(session_context, "get_turn_count", None)
        if callable(get_turn_count):
            try:
                turn_index = max(0, int(get_turn_count(session_id=session_id) or 0) - 1)
            except Exception:
                turn_index = 0
        brain_status = str(metadata.get("brain_status", "") or "").strip()
        termination_reason = str(
            metadata.get("tool_loop_termination_reason")
            or metadata.get("termination_reason")
            or ""
        ).strip()
        content = self._plan_snapshot_content(
            session_id=session_id,
            state_inline=state_inline,
            turn_index=turn_index,
            brain_status=brain_status,
            termination_reason=termination_reason,
        )
        if content is None:
            return None

        candidate_id = (
            "cand_plan_snapshot_"
            + hashlib.sha1(str(session_id).encode("utf-8")).hexdigest()[:12]
        )
        title = f"plan_snapshot:{session_id}:{content['incomplete_reason']}"
        candidate_id = self._service.candidate_put(
            MemoryCandidate(
                candidate_id=candidate_id,
                session_id=session_id,
                proposed_scope=f"agent:{self._agent_id}",
                type="plan_snapshot",
                title=title,
                content=content,
                tags=[
                    "plan_snapshot",
                    f"incomplete_reason:{content['incomplete_reason']}",
                ],
                source="validated",
                confidence=0.8,
                key=f"plan_snapshot:{session_id}",
                meta={
                    "source_session_id": session_id,
                    "source_brain_status": brain_status,
                    "source_termination_reason": termination_reason,
                    "source_turn_index": max(0, int(turn_index or 0)),
                },
            )
        )
        self._trace(
            "memory.plan_snapshot.staged",
            {
                "session_id": session_id,
                "candidate_id": candidate_id,
                "incomplete_reason": content["incomplete_reason"],
                "plan_step_count": len(list(content.get("plan_steps", []) or [])),
                "intent_count": len(list(content.get("intent_states", []) or [])),
            },
        )
        return candidate_id

    def _extract_topic_keywords(self, rolling_summary: str) -> list[str]:
        del rolling_summary
        return []

    def _normalize_summary_items(
        self,
        values: Any,
        *,
        limit: int = 5,
        max_chars: int = 120,
    ) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in values:
            text = str(item or "").strip()
            if not text:
                continue
            if len(text) > max_chars:
                text = text[:max_chars].rstrip()
            lowered = text.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(text)
            if len(normalized) >= limit:
                break
        return normalized

    def _empty_session_summary_content(
        self, *, turn_count: int
    ) -> SessionSummaryContent:
        return {
            "decisions": [],
            "open_questions": [],
            "corrections": [],
            "topic_keywords": [],
            "active_threads": [],
            "outcome": "unknown",
            "turn_count": max(0, int(turn_count)),
            "summary_text": "",
        }

    def _normalize_session_summary_outcome(self, value: Any) -> str:
        outcome = str(value or "unknown").strip().lower()
        if outcome not in {
            "succeeded",
            "blocked",
            "no_prior_context",
            "abandoned",
            "unknown",
        }:
            return "unknown"
        return outcome

    def _session_summary_outcome_rank(self, value: Any) -> int:
        outcome = self._normalize_session_summary_outcome(value)
        if outcome == "succeeded":
            return 3
        if outcome == "blocked":
            return 2
        if outcome == "unknown":
            return 1
        return 0

    def _normalize_active_threads(self, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            topic = str(item.get("topic", "") or "").strip()
            if not topic:
                continue
            status = str(item.get("status", "") or "open").strip().lower()
            if status not in {"open", "paused", "done"}:
                status = "open"
            next_step = str(item.get("next_step", "") or "").strip()
            normalized.append(
                {
                    "topic": topic[:80],
                    "status": status,
                    "next_step": next_step[:160],
                }
            )
            if len(normalized) >= 3:
                break
        return normalized

    def _active_thread_status_rank(self, value: Any) -> int:
        status = str(value or "").strip().lower()
        if status == "open":
            return 2
        if status == "paused":
            return 1
        return 0

    def _truncate_session_summary_text(
        self, text: Any, *, max_chars: int, ellipsis: bool = True
    ) -> str:
        normalized = str(text or "").strip()
        limit = max(0, int(max_chars))
        if limit <= 0:
            return ""
        if len(normalized) <= limit:
            return normalized
        if not ellipsis or limit <= 3:
            return normalized[:limit].rstrip()
        return normalized[: max(0, limit - 3)].rstrip() + "..."

    def _run_session_summary_structurer(
        self,
        structurer: Any,
        safe_summary: str,
        *,
        turn_count: int,
    ) -> dict[str, Any] | None:
        if not callable(structurer):
            return None
        if bool(getattr(self, "_session_summary_structurer_disabled", False)):
            return None

        timeout_seconds = float(
            getattr(self, "_session_summary_structurer_timeout_seconds", 0.0) or 0.0
        )
        if timeout_seconds <= 0:
            try:
                result = structurer(safe_summary, max(0, int(turn_count)))
            except Exception:
                return None
            return result if isinstance(result, dict) else None

        result_box: dict[str, Any] = {}
        error_box: dict[str, BaseException] = {}

        def _worker() -> None:
            try:
                result_box["value"] = structurer(safe_summary, max(0, int(turn_count)))
            except BaseException as exc:  # pragma: no cover - defensive
                error_box["value"] = exc

        worker = threading.Thread(
            target=_worker,
            name="session-summary-structurer",
            daemon=True,
        )
        worker.start()
        worker.join(timeout_seconds)
        if worker.is_alive():
            self._session_summary_structurer_disabled = True
            logger = getattr(self, "_logger", None)
            if logger is not None:
                try:
                    logger.warning(
                        "memory.session_summary_structurer_timeout agent_id=%s timeout_seconds=%.2f",
                        getattr(self, "_agent_id", "openminion"),
                        timeout_seconds,
                    )
                except Exception:
                    pass
            trace_fn = getattr(self, "_trace", None)
            if callable(trace_fn):
                try:
                    trace_fn(
                        "memory.session_summary_structurer.timeout",
                        {
                            "agent_id": getattr(self, "_agent_id", "openminion"),
                            "timeout_seconds": timeout_seconds,
                            "turn_count": max(0, int(turn_count)),
                        },
                    )
                except Exception:
                    pass
            return None
        if error_box:
            return None
        result = result_box.get("value")
        return result if isinstance(result, dict) else None

    def _should_skip_session_summary_structurer(
        self,
        *,
        safe_summary: str,
        turn_count: int,
    ) -> bool:
        return (
            bool(safe_summary)
            and max(0, int(turn_count))
            < int(self._SESSION_SUMMARY_STRUCTURER_MIN_TURN_COUNT)
            and len(str(safe_summary or "").strip())
            <= int(self._SESSION_SUMMARY_STRUCTURER_SHORT_SUMMARY_MAX_CHARS)
        )

    def _structure_rolling_summary(
        self,
        rolling_summary: str,
        *,
        turn_count: int = 0,
    ) -> SessionSummaryContent:
        raw_summary = str(rolling_summary or "").strip()
        safe_summary = raw_summary[: self._session_summary_max_chars]
        if not safe_summary:
            return self._empty_session_summary_content(turn_count=turn_count)

        structurer = getattr(self, "_session_summary_structurer", None)
        if self._should_skip_session_summary_structurer(
            safe_summary=safe_summary,
            turn_count=max(0, int(turn_count)),
        ):
            structured = None
        else:
            structured = self._run_session_summary_structurer(
                structurer,
                safe_summary,
                turn_count=max(0, int(turn_count)),
            )
        if isinstance(structured, dict):
            summary_text = str(structured.get("summary_text", "") or "").strip()
            if not summary_text:
                summary_text = safe_summary
            return {
                "decisions": self._normalize_summary_items(
                    structured.get("decisions", [])
                ),
                "open_questions": self._normalize_summary_items(
                    structured.get("open_questions", [])
                ),
                "corrections": self._normalize_summary_items(
                    structured.get("corrections", [])
                ),
                "topic_keywords": self._normalize_summary_items(
                    structured.get("topic_keywords", []),
                    limit=5,
                    max_chars=40,
                ),
                "active_threads": self._normalize_active_threads(
                    structured.get("active_threads", [])
                ),
                "outcome": self._normalize_session_summary_outcome(
                    structured.get("outcome", "unknown")
                ),
                "turn_count": max(0, int(turn_count)),
                "summary_text": summary_text[: self._session_summary_max_chars],
            }

        return {
            **self._empty_session_summary_content(turn_count=turn_count),
            "summary_text": safe_summary,
        }

    def write_session_summary(self, session_id: str) -> str | None:
        self.write_plan_snapshot(session_id)
        if self._session_context is None:
            return None
        build_summary_checkpoint = getattr(
            self._session_context, "build_summary_checkpoint", None
        )
        if callable(build_summary_checkpoint):
            try:
                rolling_summary, turn_count = build_summary_checkpoint(
                    session_id=session_id
                )
            except Exception:
                rolling_summary, turn_count = "", 0
        else:
            context = self._session_context.ensure_session_context(
                session_id=session_id
            )
            rolling_summary = str(getattr(context, "rolling_summary", "") or "").strip()
            turn_count = int(getattr(context, "compacted_message_count", 0) or 0)
        rolling_summary = str(rolling_summary or "").strip()
        if not rolling_summary:
            return None
        structured = self._structure_rolling_summary(
            rolling_summary,
            turn_count=turn_count,
        )
        title = (
            structured["decisions"][0]
            if structured["decisions"]
            else (structured["summary_text"][:80] or f"Session summary {session_id}")
        )
        record = self._service.upsert_record(
            scope=f"agent:{self._agent_id}",
            record_type="session_summary",
            key=f"session_summary:{session_id}",
            record_patch={
                "title": title,
                "content": dict(structured),
                "tags": ["session_summary", session_id],
                "entities": list(structured.get("topic_keywords", [])),
                "source": "validated",
                "confidence": 0.8,
            },
        )
        self._maybe_run_reflection()
        return str(getattr(record, "id", "") or None)

    def maybe_checkpoint_session_summary(self, session_id: str) -> str | None:
        session_context = getattr(self, "_session_context", None)
        if session_context is None:
            return None
        get_turn_count = getattr(session_context, "get_turn_count", None)
        if not callable(get_turn_count):
            return None
        try:
            total_messages = max(0, int(get_turn_count(session_id=session_id) or 0))
        except Exception:
            return None
        interval = max(
            1,
            int(
                getattr(
                    self,
                    "_session_summary_checkpoint_message_interval",
                    2,
                )
                or 2
            ),
        )
        if total_messages < interval or total_messages % interval != 0:
            return None
        return self.write_session_summary(session_id)

    def maybe_checkpoint_session_summary_for_token_pressure(
        self,
        session_id: str,
        *,
        token_count: int | None = None,
        token_budget: int | None = None,
        pressure_threshold: float = 0.85,
    ) -> str | None:
        session_context = getattr(self, "_session_context", None)
        if session_context is None:
            return None
        try:
            threshold = float(pressure_threshold)
        except (TypeError, ValueError):
            threshold = 0.85
        threshold = min(1.0, max(0.01, threshold))

        count = token_count
        budget = token_budget
        if count is None or budget is None:
            estimate_token_pressure = getattr(
                session_context,
                "estimate_token_pressure",
                None,
            )
            if not callable(estimate_token_pressure):
                return None
            try:
                count, budget, _pressure = estimate_token_pressure(
                    session_id=session_id
                )
            except Exception:
                return None
        try:
            count_int = max(0, int(count or 0))
            budget_int = max(0, int(budget or 0))
        except (TypeError, ValueError):
            return None
        if budget_int <= 0:
            return None
        if (count_int / float(budget_int)) < threshold:
            return None

        get_turn_count = getattr(session_context, "get_turn_count", None)
        if not callable(get_turn_count):
            return None
        try:
            total_messages = max(0, int(get_turn_count(session_id=session_id) or 0))
        except Exception:
            return None
        checkpoint_turns = getattr(
            self,
            "_session_summary_token_pressure_checkpoint_turns",
            None,
        )
        if checkpoint_turns is None:
            checkpoint_turns = {}
            self._session_summary_token_pressure_checkpoint_turns = checkpoint_turns
        if int(checkpoint_turns.get(session_id, -1)) >= total_messages:
            return None
        summary_id = self.write_session_summary(session_id)
        checkpoint_turns[session_id] = total_messages
        return summary_id

    def _format_session_summaries(
        self,
        records: list[Any],
        *,
        max_chars: int,
        current_session: bool = False,
    ) -> str:
        if not records:
            return ""
        entries: list[dict[str, Any]] = []
        for record in records:
            content = getattr(record, "content", {}) or {}
            summary_text = _content_text(content)
            title = str(getattr(record, "title", "") or "").strip()
            if not summary_text and not title:
                continue
            content_dict: dict[str, Any] = content if isinstance(content, dict) else {}
            entries.append(
                {
                    "title": title,
                    "summary_text": summary_text,
                    "decisions": list(content_dict.get("decisions", []) or []),
                    "open_questions": list(
                        content_dict.get("open_questions", []) or []
                    ),
                    "corrections": list(content_dict.get("corrections", []) or []),
                    "keywords": list(content_dict.get("topic_keywords", []) or []),
                    "active_threads": list(
                        content_dict.get("active_threads", []) or []
                    ),
                    "outcome": self._normalize_session_summary_outcome(
                        content_dict.get("outcome", "unknown")
                    ),
                }
            )
        if not entries:
            return ""
        eligible_indices = [
            index
            for index, entry in enumerate(entries)
            if entry.get("outcome") not in {"no_prior_context", "abandoned"}
        ]
        if not eligible_indices:
            return ""
        preferred_index = eligible_indices[0]
        best_rank = (-1, -1)
        for index in eligible_indices:
            entry = entries[index]
            active_threads = list(entry.get("active_threads", []) or [])
            entry_rank = 0
            for thread in active_threads:
                if not isinstance(thread, Mapping):
                    continue
                entry_rank = max(
                    entry_rank,
                    self._active_thread_status_rank(thread.get("status", "")),
                )
            candidate_rank = (
                self._session_summary_outcome_rank(entry.get("outcome", "unknown")),
                entry_rank,
            )
            if candidate_rank > best_rank:
                best_rank = candidate_rank
                preferred_index = index
        first = entries[preferred_index]
        active_thread: Mapping[str, Any] | None = None
        active_thread_rank = -1
        for thread in list(first.get("active_threads", []) or []):
            if not isinstance(thread, Mapping):
                continue
            rank = self._active_thread_status_rank(thread.get("status", ""))
            if rank > active_thread_rank:
                active_thread = thread
                active_thread_rank = rank
        if active_thread is None:
            for thread in list(first.get("active_threads", []) or []):
                if isinstance(thread, Mapping):
                    active_thread = thread
                    break
        prior_decisions: list[str] = []
        prior_open_questions: list[str] = []
        prior_corrections: list[str] = []
        eligible_entries = [entries[index] for index in eligible_indices]
        for entry in eligible_entries:
            for source_key, target in (
                ("decisions", prior_decisions),
                ("open_questions", prior_open_questions),
                ("corrections", prior_corrections),
            ):
                seen = {item.lower() for item in target}
                for item in list(entry.get(source_key, []) or []):
                    text = str(item or "").strip()
                    if not text or text.lower() in seen:
                        continue
                    target.append(text)
                    seen.add(text.lower())
                    if len(target) >= 3:
                        break
        lines = (
            [CURRENT_SESSION_SUMMARY_HEADER, "", CURRENT_SESSION_CALLBACK_CONTEXT_LABEL]
            if current_session
            else [
                PRIOR_SESSION_SUMMARY_HEADER,
                "",
                PRIOR_SESSION_CONTEXT_LABEL,
            ]
        )
        key_decision_section: list[str] | None = None
        if first["decisions"]:
            key_decision_limit = 120 if current_session else 88
            key_decision = self._truncate_session_summary_text(
                first["decisions"][0],
                max_chars=key_decision_limit,
            )
            if key_decision:
                key_decision_section = [f"  Key decision: {key_decision}"]
        summary_preview = ""
        if first["summary_text"]:
            summary_preview_limit = 88 if current_session else 120
            if active_thread is not None and not current_session:
                summary_preview_limit = 48
            summary_preview = self._truncate_session_summary_text(
                first["summary_text"],
                max_chars=summary_preview_limit,
            )
        optional_sections: list[list[str]] = []
        title_section: list[str] | None = None
        if first["title"]:
            title_section = [
                "  Title: "
                + self._truncate_session_summary_text(
                    first["title"],
                    max_chars=72,
                )
            ]
        topic_section: list[str] | None = None
        if first["keywords"]:
            topic_label = ", ".join(str(item) for item in first["keywords"][:5])
            topic_section = [
                "  Topic: "
                + self._truncate_session_summary_text(
                    topic_label,
                    max_chars=72,
                )
            ]
        elif not current_session:
            topic_section = ["  Topic: none"]
        active_thread_section: list[str] | None = None
        if active_thread is not None:
            active_thread_lines = ["", "Active thread:"]
            active_thread_lines.append(
                "  Topic: "
                + self._truncate_session_summary_text(
                    active_thread.get("topic", ""),
                    max_chars=64,
                )
            )
            active_thread_lines.append(
                f"  Status: {str(active_thread.get('status', '')).strip() or 'open'}"
            )
            next_step = str(active_thread.get("next_step", "") or "").strip()
            if next_step:
                active_thread_lines.append(
                    "  Next step: "
                    + self._truncate_session_summary_text(next_step, max_chars=120)
                )
            active_thread_section = active_thread_lines
        open_question_section: list[str] | None = None
        if first["open_questions"]:
            open_question_section = [
                "  Open question: "
                + self._truncate_session_summary_text(
                    first["open_questions"][0],
                    max_chars=96,
                )
            ]
        summary_section: list[str] | None = None
        if summary_preview:
            summary_section = [f"  Summary: {summary_preview}"]
        prioritized_sections = (
            [
                topic_section,
                key_decision_section,
                open_question_section,
                active_thread_section,
                summary_section,
                title_section,
            ]
            if current_session
            else [
                title_section,
                topic_section,
                active_thread_section,
                summary_section,
                key_decision_section,
                open_question_section,
            ]
        )
        for section in prioritized_sections:
            if section:
                optional_sections.append(section)
        for header, prior_items in (
            ("Prior decisions:", prior_decisions),
            ("Prior corrections:", prior_corrections),
            ("Open questions from earlier:", prior_open_questions),
        ):
            if not prior_items:
                continue
            optional_sections.append(
                ["", header]
                + [
                    "  - " + self._truncate_session_summary_text(item, max_chars=96)
                    for item in prior_items[:3]
                ]
            )
        if len(eligible_entries) > 1:
            remaining_entries = [
                entry
                for index, entry in enumerate(entries)
                if index in eligible_indices and index != preferred_index
            ]
            other_lines = ["", "Other recent context:"]
            for entry in remaining_entries:
                label = self._truncate_session_summary_text(
                    entry["title"] or "Session summary",
                    max_chars=48,
                )
                condensed = self._truncate_session_summary_text(
                    entry["summary_text"] or label,
                    max_chars=80,
                )
                other_lines.append(f"  • {label} — {condensed or label}")
            optional_sections.append(other_lines)
        omitted_sections = False
        for section in optional_sections:
            candidate = "\n".join(lines + section)
            if len(candidate) <= max_chars:
                lines.extend(section)
            else:
                omitted_sections = True
        rendered = "\n".join(lines)
        if omitted_sections:
            marker = "\n  [truncated]"
            if len(rendered) + len(marker) <= max_chars:
                rendered += marker
        if len(rendered) > max_chars:
            return rendered[:max_chars]
        return rendered

    def compress_old_summaries(
        self,
        *,
        max_age_days: int = 14,
        max_summary_chars: int = 100,
    ) -> tuple[int, int]:
        store = getattr(self._service, "_store", None)
        if store is None:
            return (0, 0)
        return compress_old_summaries_gc(
            store,
            max_age_days=max_age_days,
            max_summary_chars=max_summary_chars,
        )

    def _maybe_run_session_lifecycle(self, *, session_id: str) -> None:
        if self._session_lifecycle_done.get(session_id, False):
            return
        store = getattr(self._service, "_store", None)
        if store is None:
            self._session_lifecycle_done[session_id] = True
            return
        retention = getattr(self._memory_config, "retention", None)
        if retention is None or not getattr(retention, "gc_enabled", True):
            self._session_lifecycle_done[session_id] = True
            return

        now = datetime.now(timezone.utc)
        should_decay = True
        if self._last_decay_run_at:
            try:
                last_decay = datetime.fromisoformat(str(self._last_decay_run_at))
                if last_decay.tzinfo is None:
                    last_decay = last_decay.replace(tzinfo=timezone.utc)
                should_decay = (now - last_decay) >= timedelta(days=1)
            except ValueError:
                should_decay = True

        try:
            if should_decay:
                apply_confidence_decay(
                    store,
                    interval_days=int(
                        getattr(retention, "confidence_decay_interval_days", 7)
                    ),
                    decay_rate=float(getattr(retention, "confidence_decay_rate", 0.05)),
                    min_confidence=float(
                        getattr(retention, "min_confidence_eviction", 0.3)
                    ),
                    disuse_threshold_days=int(
                        getattr(retention, "disuse_threshold_days", 30)
                    ),
                    disuse_decay_multiplier=float(
                        getattr(retention, "disuse_decay_multiplier", 2.0)
                    ),
                )
                self._last_decay_run_at = now.isoformat()

            # Reads the typed
            compress_old_summaries_gc(
                store,
                max_age_days=int(retention.summary_compression_age_days),
                delete_age_days=int(retention.summary_delete_age_days),
                max_summary_chars=int(retention.summary_compression_max_chars),
            )
            enforce_scope_capacity(
                store,
                max_records=int(getattr(retention, "max_records_per_scope", 500)),
            )
            evict_stale_insights(
                store,
                staleness_days=int(getattr(retention, "insight_staleness_days", 60)),
            )
            # Retires MRCO-C — the `500` fallback
            purge_soft_deleted(
                store,
                batch_size=int(retention.gc_batch_size),
            )
            self._session_lifecycle_done[session_id] = True
        except Exception as exc:
            self._logger.warning(
                "memory.session_lifecycle failed agent_id=%s session_id=%s error=%s",
                self._agent_id,
                session_id,
                exc,
            )


__all__ = ["SessionLifecycleMixin"]
