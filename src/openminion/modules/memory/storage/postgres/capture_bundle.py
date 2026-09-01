"""Atomic PostgreSQL capture-bundle transaction."""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING, Any

from ...runtime.capture_bundle import (
    CaptureBundleInput,
    CaptureBundleIntegrityError,
    CaptureBundleReceipt,
    CaptureCandidateInput,
    capture_output_id,
    capture_result_hash,
)

if TYPE_CHECKING:
    from .store import PostgresMemoryStore


CAPTURE_BUNDLE_RECEIPT_DDL = """
CREATE TABLE IF NOT EXISTS memory_capture_bundle_receipts (
    capture_id TEXT PRIMARY KEY,
    report_hash TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    output_ids_json JSONB NOT NULL,
    disposition TEXT NOT NULL,
    committed_at TEXT NOT NULL
)
"""

_KIND_TO_RECORD_TYPE = {
    "fact": "fact",
    "user_preference": "user_preference",
    "task": "task",
}


def _receipt_from_row(row: dict[str, Any]) -> CaptureBundleReceipt:
    raw_output_ids = row["output_ids_json"]
    output_ids = (
        json.loads(str(raw_output_ids))
        if isinstance(raw_output_ids, str)
        else raw_output_ids
    )
    return CaptureBundleReceipt(
        capture_id=str(row["capture_id"]),
        report_hash=str(row["report_hash"]),
        result_hash=str(row["result_hash"]),
        output_ids=tuple(str(item) for item in output_ids),
        disposition=str(row["disposition"]),
        committed_at=str(row["committed_at"]),
    )


def _capturable_candidates(
    bundle: CaptureBundleInput,
) -> list[tuple[int, CaptureCandidateInput, str]]:
    return [
        (ordinal, item, record_type)
        for ordinal, item in enumerate(bundle.candidates)
        if (record_type := _KIND_TO_RECORD_TYPE.get(item.kind)) is not None
        and item.title.strip()
        and item.content.strip()
    ]


def _capture_result(
    bundle: CaptureBundleInput,
    candidates: list[tuple[int, CaptureCandidateInput, str]],
) -> tuple[tuple[str, ...], str, str]:
    output_ids = tuple(
        capture_output_id(
            capture_id=bundle.capture_id,
            normalized_key=item.normalized_key,
            ordinal=ordinal,
        )
        for ordinal, item, _record_type in candidates
    )
    disposition = "succeeded" if output_ids else "succeeded_no_output"
    result_hash = capture_result_hash(
        output_ids=output_ids,
        disposition=disposition,
    )
    return output_ids, disposition, result_hash


def _existing_receipt(
    store: "PostgresMemoryStore", conn: Any, bundle: CaptureBundleInput
) -> CaptureBundleReceipt | None:
    existing = store._fetchone(
        """
        SELECT capture_id, report_hash, result_hash, output_ids_json,
               disposition, committed_at
          FROM memory_capture_bundle_receipts
         WHERE capture_id = :capture_id
        """,
        {"capture_id": bundle.capture_id},
        connection=conn,
    )
    if existing is None:
        return None
    receipt = _receipt_from_row(existing)
    if receipt.report_hash != bundle.report_hash:
        raise CaptureBundleIntegrityError(
            f"capture {bundle.capture_id} was committed with another report"
        )
    return receipt


def _insert_candidates(
    store: "PostgresMemoryStore",
    conn: Any,
    bundle: CaptureBundleInput,
    candidates: list[tuple[int, CaptureCandidateInput, str]],
    output_ids: tuple[str, ...],
    now: str,
) -> None:
    for output_id, (ordinal, item, record_type) in zip(
        output_ids,
        candidates,
        strict=True,
    ):
        meta = {
            "source": "auto_extracted",
            "source_kind": item.kind,
            "source_session_id": bundle.session_id,
            "source_agent_id": bundle.agent_id,
            "source_root_turn_id": bundle.root_turn_id,
            "capture_id": bundle.capture_id,
            "capture_ordinal": ordinal,
            "normalized_key": item.normalized_key,
        }
        store._execute(
            """
            INSERT INTO memory_candidates (
                candidate_id, session_id, proposed_scope, type, key, title,
                content_json, tags_json, entities_json, source, confidence,
                evidence_json, meta_json, status, review_json, created_at,
                updated_at
            ) VALUES (
                :candidate_id, :session_id, :proposed_scope, :record_type,
                :key, :title, CAST(:content_json AS JSONB),
                CAST(:tags_json AS JSONB), CAST(:entities_json AS JSONB),
                :source, :confidence, CAST(:evidence_json AS JSONB),
                CAST(:meta_json AS JSONB), :status, NULL, :created_at,
                :updated_at
            )
            """,
            {
                "candidate_id": output_id,
                "session_id": bundle.session_id,
                "proposed_scope": f"agent:{bundle.agent_id}",
                "record_type": record_type,
                "key": item.normalized_key,
                "title": item.title.strip(),
                "content_json": json.dumps(item.content.strip()),
                "tags_json": json.dumps(list(item.tags)),
                "entities_json": "[]",
                "source": "agent_inferred",
                "confidence": max(0.0, min(1.0, float(item.confidence))),
                "evidence_json": json.dumps(
                    [
                        {
                            "ref": bundle.capture_id,
                            "mime": "application/x-openminion-capture",
                            "sha256": bundle.report_hash,
                            "size_bytes": 0,
                            "label": "assured-capture",
                        }
                    ]
                ),
                "meta_json": json.dumps(meta, sort_keys=True),
                "status": "proposed",
                "created_at": now,
                "updated_at": now,
            },
            connection=conn,
        )


def _insert_receipt(
    store: "PostgresMemoryStore",
    conn: Any,
    bundle: CaptureBundleInput,
    output_ids: tuple[str, ...],
    disposition: str,
    result_hash: str,
    now: str,
) -> None:
    store._execute(
        """
        INSERT INTO memory_capture_bundle_receipts (
            capture_id, report_hash, result_hash, output_ids_json,
            disposition, committed_at
        ) VALUES (
            :capture_id, :report_hash, :result_hash,
            CAST(:output_ids_json AS JSONB), :disposition, :committed_at
        )
        """,
        {
            "capture_id": bundle.capture_id,
            "report_hash": bundle.report_hash,
            "result_hash": result_hash,
            "output_ids_json": json.dumps(list(output_ids)),
            "disposition": disposition,
            "committed_at": now,
        },
        connection=conn,
    )


def apply_capture_bundle(
    store: "PostgresMemoryStore", bundle: CaptureBundleInput
) -> CaptureBundleReceipt:
    """Commit candidates and their immutable receipt in one transaction."""

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    candidates = _capturable_candidates(bundle)
    output_ids, disposition, result_hash = _capture_result(bundle, candidates)

    with store._lock, store._engine.begin() as conn:
        store._execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(:capture_id, 0))",
            {"capture_id": bundle.capture_id},
            connection=conn,
        )
        if receipt := _existing_receipt(store, conn, bundle):
            return receipt
        _insert_candidates(store, conn, bundle, candidates, output_ids, now)
        _insert_receipt(store, conn, bundle, output_ids, disposition, result_hash, now)

    return CaptureBundleReceipt(
        capture_id=bundle.capture_id,
        report_hash=bundle.report_hash,
        result_hash=result_hash,
        output_ids=output_ids,
        disposition=disposition,
        committed_at=now,
    )


__all__ = ["CAPTURE_BUNDLE_RECEIPT_DDL", "apply_capture_bundle"]
