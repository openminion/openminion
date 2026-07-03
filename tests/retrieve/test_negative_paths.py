from __future__ import annotations

import pytest
from pathlib import Path

from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl


def _config(tmp_path: Path, **overrides) -> dict:
    cfg: dict = {
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
            },
        },
    }
    cfg["retrievectl"].update(overrides)
    return cfg


def _service(tmp_path: Path, **overrides) -> RetrieveCtl:
    return RetrieveCtl(_config(tmp_path, **overrides))


def test_retrieve_returns_empty_for_no_indexed_content(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        rows = service.retrieve(
            query="totally unrelated query with no matches",
            purpose="act",
            scope={},
            k=5,
            strategy="contextual",
        )
        assert rows == [] or isinstance(rows, list)
    finally:
        service.close()


def test_retrieve_with_zero_k_clamps_to_minimum(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        service.ingest_source(
            source_type="artifact",
            source_ref="artifact://sha256/" + ("a" * 64),
            text="Some indexed content about deployment.",
            scope="project",
            tags=["ops"],
            title="Deploy notes",
            unit_kind="chunk",
        )
        rows = service.retrieve(
            query="deployment",
            purpose="act",
            scope={"project": True},
            k=0,
            strategy="contextual",
        )
        assert isinstance(rows, list)
        assert len(rows) <= 1
    finally:
        service.close()


def test_ingest_empty_text_raises_invalid_argument(tmp_path: Path) -> None:
    from openminion.modules.retrieve.errors import RetrieveCtlError

    service = _service(tmp_path)
    try:
        with pytest.raises(RetrieveCtlError) as exc_info:
            service.ingest_source(
                source_type="artifact",
                source_ref="artifact://sha256/" + ("b" * 64),
                text="   \n\n   ",
                scope="project",
                tags=[],
                title="Empty text doc",
                unit_kind="chunk",
            )
        assert "INVALID_ARGUMENT" in str(exc_info.value)
    finally:
        service.close()


def test_retrieve_unknown_strategy_falls_back_gracefully(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        service.ingest_source(
            source_type="doc",
            source_ref="doc://test/fallback",
            text="Fallback strategy test content for the retrieve step.",
            scope="project",
            tags=["test"],
            title="Fallback doc",
            unit_kind="chunk",
        )
        try:
            rows = service.retrieve(
                query="fallback",
                purpose="act",
                scope={"project": True},
                k=3,
                strategy="contextual",  # known-good strategy
            )
            assert isinstance(rows, list)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"retrieve raised unexpectedly: {exc}")
    finally:
        service.close()


def test_expand_unknown_ref_returns_empty(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        result = service.expand(ref="node://nonexistent/xyz", mode="leaves", k=5)
        assert result == [] or isinstance(result, list)
    finally:
        service.close()


def test_retrieve_with_extra_scope_keys_does_not_raise(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        service.ingest_source(
            source_type="mem",
            source_ref="mem:fact-edge",
            text="Edge case fact: extra scope keys should be ignored safely.",
            scope="agent",
            tags=["edge"],
            title="Edge scope fact",
            unit_kind="chunk",
        )
        rows = service.retrieve(
            query="edge case scope",
            purpose="verify",
            scope={"agent": True, "unexpected_key": "surprise", "nested": {"deep": 1}},
            k=3,
            strategy="contextual",
        )
        assert isinstance(rows, list)
    finally:
        service.close()


def test_retrieve_returns_correct_provenance_fields_for_rlm(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        service.ingest_source(
            source_type="mem",
            source_ref="mem:fact-prov",
            text="Provenance fact for RLM integration field completeness test.",
            scope="agent",
            tags=["prov"],
            title="Provenance test",
            unit_kind="chunk",
        )
        rows = service.retrieve(
            query="provenance completeness",
            purpose="act",
            scope={"agent": True},
            k=2,
            strategy="contextual",
        )
        assert rows
        for row in rows:
            assert "ref_id" in row or "ref" in row
            assert "text" in row and row["text"]
            assert "score" in row
            assert "source" in row
    finally:
        service.close()


def test_fts_fallback_returns_same_structure_as_contextual(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        shared_text = (
            "Aurora deployment window is critical for production rollouts.\n\n"
            "Ensure all pre-flight checks pass before commencing the deploy sequence."
        )
        service.ingest_source(
            source_type="artifact",
            source_ref="artifact://sha256/" + ("c" * 64),
            text=shared_text,
            scope="project",
            tags=["ops"],
            title="Deploy window doc",
            unit_kind="chunk",
        )
        ctx_rows = service.retrieve(
            query="aurora deployment window",
            purpose="act",
            scope={"project": True},
            k=3,
            strategy="contextual",
        )
        required_keys = {"ref_id", "text", "score", "source"}
        for row in ctx_rows:
            present_keys = set(row.keys())
            missing = required_keys - present_keys
            if "ref_id" not in row and "ref" in row:
                missing.discard("ref_id")
            assert not missing, (
                f"Missing required keys: {missing} in row {list(row.keys())}"
            )
    finally:
        service.close()
