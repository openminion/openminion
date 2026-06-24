from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl
from openminion.modules.retrieve.diagnostics.events import (
    emit_retrieve_operation,
)
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _config(tmp_path: Path) -> dict:
    return {
        "version": 1,
        "retrievectl": {
            "storage": {
                "sqlite_path": str(tmp_path / "retrievectl.db"),
                "blob_root": str(tmp_path / "blob"),
                "wal_mode": False,
            },
            "defaults": {
                "strategy": "contextual",
                "contextual_enabled": True,
                "embeddings_enabled": False,
                "lexical_candidate_count": 25,
                "snippet_tokens": 120,
                "chunk_target_tokens": 30,
                "chunk_min_tokens": 15,
                "chunk_max_tokens": 35,
                "doc_group_target_tokens": 40,
                "doc_group_min_tokens": 25,
                "doc_group_max_tokens": 60,
                "raptor_internal_k": 2,
                "raptor_leaf_k": 4,
                # `recency_half_life_hours` dropped;
                # telemetry-ops test is not recency-behavior-sensitive.
            },
        },
    }


def test_retrieve_service_emits_query_rerank_and_fallback(tmp_path: Path) -> None:
    telemetry = TelemetryService(str(tmp_path / ".openminion" / "telemetry.db"))
    ctl = TelemetryCtl(telemetry)
    service = RetrieveCtl(_config(tmp_path), telemetryctl=ctl)
    service.set_telemetry_context(session_id="sess-retrieve", turn_id="turn-1")
    service.ingest_source(
        source_type="artifact",
        source_ref="artifact://sha256/" + ("a" * 64),
        text=(
            "Aurora deploy handbook.\n\n"
            "Checklist: run preflight tests, validate migration steps, then deploy.\n\n"
            "Rollback plan: restore previous release artifact and replay migrations."
        ),
        scope="session:sess-retrieve",
        tags=["ops", "deploy"],
        title="Aurora deployment handbook",
        unit_kind="chunk",
    )

    rows = service.retrieve(
        query="aurora rollback checklist",
        purpose="act",
        scope={"session_id": "sess-retrieve", "scope": "session:sess-retrieve"},
        k=3,
        strategy="contextual",
    )
    assert rows

    missing = service.retrieve(
        query="totally unrelated missing topic",
        purpose="act",
        scope={"session_id": "sess-retrieve", "scope": "session:sess-retrieve"},
        k=3,
        strategy="contextual",
    )
    assert isinstance(missing, list)

    summary = _run(telemetry.get_module_summary("sess-retrieve"))
    stats = summary["openminion-retrieve"]
    assert stats["operation_counts"]["query"] >= 2
    assert stats["operation_counts"]["rerank"] >= 2
    assert stats["operation_counts"]["fallback"] >= 1
    assert stats["custom_counter_sums"]["returned_items"] >= 0.0
    assert stats["custom_counter_sums"]["latency_bucket_ms"] >= 0.0
    assert stats["custom_counter_sums"]["token_estimate"] >= 1.0

    service.close()
    _run(telemetry.close())


def test_retrieve_helper_rejects_unknown_operation_and_absent_adapter() -> None:
    assert (
        emit_retrieve_operation(
            telemetryctl=None,
            session_id="sess-retrieve-invalid",
            turn_id="turn-1",
            operation="",
        )
        is False
    )
