"""Memory-owned contracts for atomic assured capture bundles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from openminion.modules.memory.errors import (
    CaptureBundleIntegrityError as CaptureBundleIntegrityError,
    CaptureRecoveryUnsupportedError as CaptureRecoveryUnsupportedError,
)


def _stable_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CaptureCandidateInput:
    kind: str
    normalized_key: str
    title: str
    content: str
    tags: tuple[str, ...] = ()
    confidence: float = 0.3

    def as_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "normalized_key": self.normalized_key,
            "title": self.title,
            "content": self.content,
            "tags": list(self.tags),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class CaptureBundleInput:
    capture_id: str
    root_turn_id: str
    session_id: str
    agent_id: str
    candidates: tuple[CaptureCandidateInput, ...]

    @property
    def report_hash(self) -> str:
        return _stable_hash(
            {
                "root_turn_id": self.root_turn_id,
                "session_id": self.session_id,
                "agent_id": self.agent_id,
                "candidates": [item.as_payload() for item in self.candidates],
            }
        )


@dataclass(frozen=True)
class CaptureBundleReceipt:
    capture_id: str
    report_hash: str
    result_hash: str
    output_ids: tuple[str, ...]
    disposition: str
    committed_at: str

    def as_payload(self) -> dict[str, object]:
        return {
            "capture_id": self.capture_id,
            "report_hash": self.report_hash,
            "result_hash": self.result_hash,
            "output_ids": list(self.output_ids),
            "disposition": self.disposition,
            "committed_at": self.committed_at,
        }


def capture_output_id(*, capture_id: str, normalized_key: str, ordinal: int) -> str:
    token = _stable_hash((capture_id, normalized_key, ordinal))[:24]
    return f"cand_{token}"


def capture_result_hash(*, output_ids: tuple[str, ...], disposition: str) -> str:
    return _stable_hash({"output_ids": list(output_ids), "disposition": disposition})
