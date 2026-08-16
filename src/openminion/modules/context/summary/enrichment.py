from __future__ import annotations

from collections.abc import Callable
from threading import Thread
from typing import Any
from uuid import uuid4


def _release_summary_lease(service: Any, session_id: str, lease: Any) -> None:
    if lease is None:
        return
    release_lease = getattr(service._sessions, "release_session_turn_lease", None)
    if callable(release_lease):
        release_lease(
            session_id,
            owner=str(lease.owner),
            fence_token=int(lease.fence_token),
        )


def maybe_schedule_summary_enrichment(
    service: Any,
    *,
    session_id: str,
    deterministic_summary: str,
    busy_error: type[Exception],
) -> None:
    if not service._summary_enrichment_enabled or service._summary_enricher is None:
        return
    summary_enricher = service._summary_enricher
    base_summary = str(deterministic_summary or "").strip()
    if not base_summary:
        return

    def _task() -> None:
        try:
            enriched = str(summary_enricher(base_summary) or "").strip()
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            service._logger.warning(
                "session summary enrichment failed session_id=%s error=%s",
                session_id,
                exc,
            )
            return
        if not enriched or enriched == base_summary:
            return
        safe_summary = enriched[-service._summary_max_chars :]
        lease = None
        lease_request_id = f"summary-enrichment:{uuid4().hex}"
        acquire_lease = getattr(service._sessions, "acquire_session_turn_lease", None)
        if callable(acquire_lease):
            try:
                lease = acquire_lease(
                    session_id,
                    owner=lease_request_id,
                    request_id=lease_request_id,
                    ttl_s=60,
                )
            except busy_error:
                service._logger.debug(
                    "session summary enrichment skipped for active turn session_id=%s",
                    session_id,
                )
                return
        else:
            service._logger.warning(
                "session turn lease unavailable; summary enrichment proceeds "
                "without maintenance fencing session_id=%s",
                session_id,
            )
        fence_token = int(lease.fence_token) if lease is not None else None
        try:
            context = service._sessions.ensure_session_context(
                session_id=session_id,
                session_turn_fence_token=fence_token,
            )
            if context.rolling_summary.strip() != base_summary:
                return
            service._sessions.update_session_context(
                session_id=session_id,
                summary_short=summary_short_from_rolling_summary(safe_summary),
                rolling_summary=safe_summary,
                compacted_until_rowid=context.compacted_until_rowid,
                compacted_until_created_at=context.compacted_until_created_at,
                compacted_until_message_id=context.compacted_until_message_id,
                compacted_message_count=context.compacted_message_count,
                version=context.version + 1,
                expected_version=context.version,
                session_turn_fence_token=fence_token,
            )
            service._sessions.append_event(
                session_id=session_id,
                event_type="session.summary.enriched",
                payload={"mode": "deferred", "chars": len(safe_summary)},
                session_turn_fence_token=fence_token,
            )
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            service._logger.warning(
                "session summary enrichment apply failed session_id=%s error=%s",
                session_id,
                exc,
            )
        finally:
            _release_summary_lease(service, session_id, lease)

    try:
        service._summary_enrichment_defer(_task)
    except RuntimeError as exc:
        service._logger.warning(
            "session summary enrichment scheduling failed session_id=%s error=%s",
            session_id,
            exc,
        )


def defer_summary_task(task: Callable[[], None]) -> None:
    Thread(target=task, daemon=True).start()


def summary_short_from_rolling_summary(rolling_summary: str) -> str:
    summary = str(rolling_summary or "").strip()
    if not summary:
        return ""
    first_line = summary.splitlines()[0].strip()
    return first_line[:240] if first_line else ""


__all__ = [
    "defer_summary_task",
    "maybe_schedule_summary_enrichment",
    "summary_short_from_rolling_summary",
]
