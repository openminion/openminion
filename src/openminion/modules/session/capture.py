"""Typed identity and receipt contracts for assured turn capture."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict, runtime_checkable

from .interfaces import TERMINAL_CAPTURE_INTENT_VERSION

CAPTURE_INTENT_SCHEMA_VERSION = TERMINAL_CAPTURE_INTENT_VERSION


class CaptureRunKwargs(TypedDict):
    runtime_session_id: str | None
    root_turn_id: str | None
    capture_event_id: str | None
    capture_id: str | None


def _digest(parts: tuple[str, ...]) -> str:
    encoded = json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CaptureIdentity:
    runtime_session_id: str
    root_turn_id: str
    event_id: str
    capture_id: str
    schema_version: str = CAPTURE_INTENT_SCHEMA_VERSION


def build_capture_identity(
    *, runtime_session_id: str, root_turn_id: str
) -> CaptureIdentity:
    session_id = str(runtime_session_id or "").strip()
    turn_id = str(root_turn_id or "").strip()
    if not session_id or not turn_id:
        raise ValueError("runtime_session_id and root_turn_id are required")
    token = _digest((CAPTURE_INTENT_SCHEMA_VERSION, session_id, turn_id))
    return CaptureIdentity(
        runtime_session_id=session_id,
        root_turn_id=turn_id,
        event_id=f"turn.outcome:{token}",
        capture_id=f"capture:{token}",
    )


def capture_run_kwargs(metadata: dict[str, Any]) -> CaptureRunKwargs:
    return {
        "runtime_session_id": str(metadata.get("runtime_session_id") or "").strip()
        or None,
        "root_turn_id": str(metadata.get("root_turn_id") or "").strip() or None,
        "capture_event_id": str(metadata.get("capture_event_id") or "").strip() or None,
        "capture_id": str(metadata.get("capture_id") or "").strip() or None,
    }


def capture_identity_metadata(
    *, runtime_session_id: str, root_turn_id: str
) -> dict[str, str]:
    identity = build_capture_identity(
        runtime_session_id=runtime_session_id,
        root_turn_id=root_turn_id,
    )
    return {
        "runtime_session_id": identity.runtime_session_id,
        "root_turn_id": identity.root_turn_id,
        "capture_event_id": identity.event_id,
        "capture_id": identity.capture_id,
    }


def capture_response_metadata(step_output: Any) -> dict[str, str]:
    metadata: dict[str, str] = {}
    receipt = getattr(step_output, "terminal_capture_intent_receipt", None)
    if receipt is not None:
        metadata["terminal_capture_intent_receipt"] = json.dumps(
            receipt.as_payload(), sort_keys=True
        )
    bundle_result = getattr(step_output, "memory_capture_bundle_result", None)
    if bundle_result is not None:
        metadata["memory_capture_bundle_result"] = json.dumps(
            bundle_result, sort_keys=True
        )
    return metadata


def capture_is_excluded(metadata: dict[str, Any]) -> bool:
    return str(metadata.get("memory_consolidation_job", "") or "").strip().lower() == (
        "true"
    )


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class TerminalCaptureIntentReceipt:
    runtime_session_id: str
    root_turn_id: str
    event_id: str
    capture_id: str
    state: Literal["pending", "excluded"]
    payload_hash: str
    schema_version: str = CAPTURE_INTENT_SCHEMA_VERSION

    def as_payload(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "runtime_session_id": self.runtime_session_id,
            "root_turn_id": self.root_turn_id,
            "event_id": self.event_id,
            "capture_id": self.capture_id,
            "state": self.state,
            "payload_hash": self.payload_hash,
        }


@runtime_checkable
class TerminalCaptureIntentWriter(Protocol):
    def commit_terminal_capture_intent(
        self,
        *,
        identity: CaptureIdentity,
        event_payload: dict[str, Any],
        state: Literal["pending", "excluded"] = "pending",
    ) -> TerminalCaptureIntentReceipt: ...

    def commit_capture_result_and_release_hold(
        self,
        *,
        identity: CaptureIdentity,
        result_payload: dict[str, Any],
    ) -> None: ...


class RuntimeTerminalCaptureWriter:
    def __init__(self, sessions: Any) -> None:
        self._sessions = sessions

    def commit_terminal_capture_intent(
        self,
        *,
        identity: CaptureIdentity,
        event_payload: dict[str, Any],
        state: Literal["pending", "excluded"] = "pending",
    ) -> TerminalCaptureIntentReceipt:
        payload = {
            **event_payload,
            "schema_version": identity.schema_version,
            "runtime_session_id": identity.runtime_session_id,
            "root_turn_id": identity.root_turn_id,
            "capture_id": identity.capture_id,
            "capture_state": state,
        }
        payload_hash = canonical_payload_hash(payload)
        event = self._sessions.commit_terminal_capture_intent(
            session_id=identity.runtime_session_id,
            canonical_event_id=identity.event_id,
            capture_id=identity.capture_id,
            payload=payload,
            payload_hash=payload_hash,
        )
        if event.canonical_event_id != identity.event_id:
            raise RuntimeError("terminal capture event identity mismatch")
        return TerminalCaptureIntentReceipt(
            runtime_session_id=identity.runtime_session_id,
            root_turn_id=identity.root_turn_id,
            event_id=identity.event_id,
            capture_id=identity.capture_id,
            state=state,
            payload_hash=payload_hash,
        )

    def commit_capture_result_and_release_hold(
        self,
        *,
        identity: CaptureIdentity,
        result_payload: dict[str, Any],
    ) -> None:
        self._sessions.commit_capture_result_and_release_hold(
            session_id=identity.runtime_session_id,
            canonical_event_id=f"memory.capture.result:{identity.capture_id}",
            capture_id=identity.capture_id,
            payload={
                "schema_version": identity.schema_version,
                "runtime_session_id": identity.runtime_session_id,
                "root_turn_id": identity.root_turn_id,
                "capture_id": identity.capture_id,
                **result_payload,
            },
        )


class TerminalCaptureReceiptError(RuntimeError):
    code = "TERMINAL_CAPTURE_RECEIPT_INVALID"


def verify_terminal_capture_receipt(
    *,
    sessions: Any,
    response_metadata: dict[str, str],
    session_id: str,
    run_id: str,
    required: bool = True,
) -> None:
    raw = str(response_metadata.get("terminal_capture_intent_receipt") or "").strip()
    if not raw:
        if required:
            raise TerminalCaptureReceiptError("terminal capture receipt is missing")
        return
    try:
        receipt = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TerminalCaptureReceiptError(
            "terminal capture receipt is not JSON"
        ) from exc
    if not isinstance(receipt, dict):
        raise TerminalCaptureReceiptError("terminal capture receipt must be an object")
    identity = build_capture_identity(
        runtime_session_id=session_id,
        root_turn_id=run_id,
    )
    expected = {
        "schema_version": CAPTURE_INTENT_SCHEMA_VERSION,
        "runtime_session_id": session_id,
        "root_turn_id": run_id,
        "event_id": identity.event_id,
        "capture_id": identity.capture_id,
    }
    for key, value in expected.items():
        if str(receipt.get(key) or "") != value:
            raise TerminalCaptureReceiptError(
                f"terminal capture receipt {key} mismatch"
            )
    event = sessions.get_event_by_canonical_id(identity.event_id)
    if event is None or event.session_id != session_id:
        raise TerminalCaptureReceiptError("terminal capture event was not persisted")
    field_pairs = {
        "schema_version": "schema_version",
        "runtime_session_id": "runtime_session_id",
        "root_turn_id": "root_turn_id",
        "capture_id": "capture_id",
        "state": "capture_state",
    }
    for receipt_key, event_key in field_pairs.items():
        if str(receipt.get(receipt_key) or "") != str(
            event.payload.get(event_key) or ""
        ):
            raise TerminalCaptureReceiptError(
                f"terminal capture receipt {receipt_key} does not match event"
            )
    if str(receipt.get("payload_hash") or "") != canonical_payload_hash(event.payload):
        raise TerminalCaptureReceiptError(
            "terminal capture receipt payload hash does not match event"
        )
