"""Submission queue with retry and audit tracking."""

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque

from openminion.base.time import utc_now_iso
from openminion.modules.memory.submissions.envelope import SubmissionEnvelope
from openminion.modules.memory.submissions.sdk_path import (
    SubmissionResult,
    submit_envelope,
)


@dataclass(frozen=True)
class QueueAuditEntry:
    """One audit row recording the outcome of a single attempt."""

    envelope_idempotency_key: str
    payload_kind: str
    attempt: int
    timestamp: str
    code: str
    ok: bool
    exhausted: bool = False
    error_type: str | None = None
    error_message: str | None = None


@dataclass
class _QueueItem:
    envelope: SubmissionEnvelope
    attempts: int = 0


@dataclass
class SubmissionQueue:
    """In-memory queue with bounded retry + audit."""

    max_attempts: int = 3
    _items: Deque[_QueueItem] = field(default_factory=deque)
    _audit: list[QueueAuditEntry] = field(default_factory=list)
    _completed_keys: set[tuple[str, str]] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.max_attempts <= 0:
            raise ValueError(  # allow-bare-raise: dataclass constructor guard
                "max_attempts must be positive"
            )

    def enqueue(self, envelope: SubmissionEnvelope) -> None:
        """Append an envelope to the queue."""
        self._items.append(_QueueItem(envelope=envelope))

    def __len__(self) -> int:
        return len(self._items)

    @property
    def audit(self) -> list[QueueAuditEntry]:
        return list(self._audit)

    def _append_audit(
        self,
        envelope: SubmissionEnvelope,
        *,
        attempt: int,
        code: str,
        ok: bool,
        exhausted: bool = False,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self._audit.append(
            QueueAuditEntry(
                envelope_idempotency_key=envelope.idempotency_key,
                payload_kind=envelope.payload_kind,
                attempt=attempt,
                timestamp=utc_now_iso(),
                code=code,
                ok=ok,
                exhausted=exhausted,
                error_type=error_type,
                error_message=error_message,
            )
        )

    def drain(self, store: Any) -> list[SubmissionResult]:
        """Run every queued envelope against ``store``."""

        results: list[SubmissionResult] = []
        while self._items:
            item = self._items.popleft()
            envelope = item.envelope
            dedup = (envelope.payload_kind, envelope.idempotency_key)
            if dedup in self._completed_keys:
                self._append_audit(
                    envelope,
                    attempt=item.attempts,
                    code="DEDUPED",
                    ok=True,
                )
                results.append(
                    SubmissionResult(
                        ok=True,
                        envelope_idempotency_key=envelope.idempotency_key,
                        payload_kind=envelope.payload_kind,
                        code="DEDUPED",
                        deduped=True,
                    )
                )
                continue
            final_result = self._run_with_retry(store, item)
            if final_result.ok:
                self._completed_keys.add(dedup)
            results.append(final_result)
        return results

    def _run_with_retry(
        self,
        store: Any,
        item: _QueueItem,
    ) -> SubmissionResult:
        last_result: SubmissionResult | None = None
        while item.attempts < self.max_attempts:
            item.attempts += 1
            result = submit_envelope(store, item.envelope)
            self._append_audit(
                item.envelope,
                attempt=item.attempts,
                code=result.code,
                ok=result.ok,
                error_type=result.error_type,
                error_message=result.error_message,
            )
            last_result = result
            if result.ok:
                return result
            if result.code == "VALIDATION_ERROR":
                # Validation errors are deterministic; no point retrying.
                break
        self._append_audit(
            item.envelope,
            attempt=item.attempts,
            code=last_result.code if last_result else "UNKNOWN",
            ok=False,
            exhausted=True,
            error_type=last_result.error_type if last_result else None,
            error_message=last_result.error_message if last_result else None,
        )
        return last_result or SubmissionResult(
            ok=False,
            envelope_idempotency_key=item.envelope.idempotency_key,
            payload_kind=item.envelope.payload_kind,
            code="UNKNOWN",
        )


__all__ = (
    "QueueAuditEntry",
    "SubmissionQueue",
)
