"""Atomic SQLite capture-bundle transaction."""

from __future__ import annotations

import datetime
import json
import sqlite3
from typing import TYPE_CHECKING

from ...runtime.capture_bundle import (
    CaptureBundleInput,
    CaptureBundleIntegrityError,
    CaptureBundleReceipt,
    CaptureCandidateInput,
    capture_output_id,
    capture_result_hash,
)

if TYPE_CHECKING:
    from .store import SQLiteMemoryStore

_KIND_TO_RECORD_TYPE = {
    "fact": "fact",
    "user_preference": "user_preference",
    "task": "task",
}


def _receipt_from_row(row: sqlite3.Row) -> CaptureBundleReceipt:
    return CaptureBundleReceipt(
        capture_id=str(row["capture_id"]),
        report_hash=str(row["report_hash"]),
        result_hash=str(row["result_hash"]),
        output_ids=tuple(json.loads(str(row["output_ids_json"]))),
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
    result_hash = capture_result_hash(output_ids=output_ids, disposition=disposition)
    return output_ids, disposition, result_hash


def _existing_receipt(
    conn: sqlite3.Connection, bundle: CaptureBundleInput
) -> CaptureBundleReceipt | None:
    existing = conn.execute(
        "SELECT * FROM memory_capture_bundle_receipts WHERE capture_id = ?",
        (bundle.capture_id,),
    ).fetchone()
    if existing is None:
        return None
    receipt = _receipt_from_row(existing)
    if receipt.report_hash != bundle.report_hash:
        raise CaptureBundleIntegrityError(
            f"capture {bundle.capture_id} was committed with another report"
        )
    return receipt


def _insert_candidates(
    conn: sqlite3.Connection,
    bundle: CaptureBundleInput,
    candidates: list[tuple[int, CaptureCandidateInput, str]],
    output_ids: tuple[str, ...],
    now: str,
) -> None:
    for candidate_id, (ordinal, item, record_type) in zip(
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
        conn.execute(
            """
            INSERT INTO memory_candidates (
                candidate_id, session_id, proposed_scope, type, key, title,
                content_json, tags_json, entities_json, source, confidence,
                evidence_json, meta_json, status, review_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                bundle.session_id,
                f"agent:{bundle.agent_id}",
                record_type,
                item.normalized_key,
                item.title.strip(),
                json.dumps(item.content.strip()),
                json.dumps(list(item.tags)),
                "[]",
                "agent_inferred",
                max(0.0, min(1.0, float(item.confidence))),
                json.dumps(
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
                json.dumps(meta, sort_keys=True),
                "proposed",
                None,
                now,
                now,
            ),
        )


def _insert_receipt(
    conn: sqlite3.Connection,
    bundle: CaptureBundleInput,
    output_ids: tuple[str, ...],
    disposition: str,
    result_hash: str,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO memory_capture_bundle_receipts (
            capture_id, report_hash, result_hash, output_ids_json,
            disposition, committed_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            bundle.capture_id,
            bundle.report_hash,
            result_hash,
            json.dumps(list(output_ids)),
            disposition,
            now,
        ),
    )


def apply_capture_bundle(
    store: "SQLiteMemoryStore", bundle: CaptureBundleInput
) -> CaptureBundleReceipt:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    candidates = _capturable_candidates(bundle)
    output_ids, disposition, result_hash = _capture_result(bundle, candidates)

    with store._write_lock, store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if receipt := _existing_receipt(conn, bundle):
                conn.execute("COMMIT")
                return receipt
            _insert_candidates(conn, bundle, candidates, output_ids, now)
            _insert_receipt(conn, bundle, output_ids, disposition, result_hash, now)
            conn.execute("COMMIT")
        except (sqlite3.Error, CaptureBundleIntegrityError):
            conn.execute("ROLLBACK")
            raise
    return CaptureBundleReceipt(
        capture_id=bundle.capture_id,
        report_hash=bundle.report_hash,
        result_hash=result_hash,
        output_ids=output_ids,
        disposition=disposition,
        committed_at=now,
    )
