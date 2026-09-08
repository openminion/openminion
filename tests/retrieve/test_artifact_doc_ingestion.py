from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import Mock

import pytest

from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl


def _artifact_config(tmp_path: Path) -> dict[str, object]:
    store_root = tmp_path / ".openminion" / "artifact-store"
    return {
        "artifactctl": {
            "blob_store": {
                "backend": "filesystem_cas",
                "root_dir": str(store_root),
            },
            "index": {
                "backend": "sqlite",
                "sqlite_path": str(store_root / "index.db"),
                "wal": False,
                "fts": False,
            },
            "views": {"auto_generate": []},
            "security": {"store_original_path": False},
        }
    }


def _retrieve_config(tmp_path: Path) -> dict[str, object]:
    retrieve_root = tmp_path / ".openminion" / "retrieve-store"
    return {
        "version": 1,
        "retrievectl": {
            "storage": {
                "sqlite_path": str(retrieve_root / "retrievectl.db"),
                "blob_root": str(retrieve_root / "blob"),
                "wal_mode": False,
            },
            "defaults": {
                "strategy": "contextual",
                "contextual_enabled": True,
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


def test_artifact_creation_fires_ingest_event(tmp_path: Path) -> None:
    src = tmp_path / "artifact.txt"
    src.write_text("artifact text for retrieval wiring", encoding="utf-8")

    retrieve_ctl = Mock()
    ctl = ArtifactCtl(_artifact_config(tmp_path))
    try:
        ref = ctl.ingest_file(
            src,
            meta={"scope": "agent:test", "title": "artifact.txt"},
            retrieve_ctl=retrieve_ctl,
        )
        retrieve_ctl.ingest_event.assert_called_once()
        event_type, payload = retrieve_ctl.ingest_event.call_args.args
        assert event_type == "artifact.created"
        assert payload["artifact_ref"] == ref.ref
        assert payload["text"] == "artifact text for retrieval wiring"
        assert payload["scope"] == "agent:test"
        assert payload["title"] == "artifact.txt"
        assert payload["tags"] == ["artifact"]
        assert ctl.list_views(ref.sha256) == []
    finally:
        ctl.close()


def test_artifact_creation_no_ctl_is_noop(tmp_path: Path) -> None:
    src = tmp_path / "artifact-noctl.txt"
    src.write_text("artifact without retrieve ctl", encoding="utf-8")

    ctl = ArtifactCtl(_artifact_config(tmp_path))
    try:
        ref = ctl.ingest_file(src, retrieve_ctl=None)
        assert ref.ref.startswith("artifact://sha256/")
    finally:
        ctl.close()


def test_artifact_ingest_event_error_does_not_propagate(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    src = tmp_path / "artifact-error.txt"
    private_text = "artifact survives callback failure"
    callback_secret = "callback leaked private payload"
    src.write_text(private_text, encoding="utf-8")

    retrieve_ctl = Mock()
    retrieve_ctl.ingest_event.side_effect = RuntimeError(callback_secret)

    ctl = ArtifactCtl(_artifact_config(tmp_path))
    try:
        with caplog.at_level(logging.WARNING):
            ref = ctl.ingest_file(src, retrieve_ctl=retrieve_ctl)
        meta = ctl.get(ref.sha256)
        assert meta.sha256 == ref.sha256
        assert len(caplog.records) == 1
        record = caplog.records[0]
        warning = record.getMessage()
        assert ref.ref in warning
        assert "RuntimeError" in warning
        assert record.exc_info is None
        assert private_text not in warning
        assert callback_secret not in warning
        assert private_text not in caplog.text
        assert callback_secret not in caplog.text
    finally:
        ctl.close()


@pytest.mark.parametrize(
    ("redaction_enabled", "expected_text"),
    [
        (True, "contact [REDACTED_EMAIL] card [REDACTED_NUMBER]"),
        (False, "contact user@example.com card 4242424242424242"),
    ],
)
def test_artifact_ingest_event_respects_redaction_config(
    tmp_path: Path, redaction_enabled: bool, expected_text: str
) -> None:
    config = _artifact_config(tmp_path)
    config["artifactctl"]["security"]["redaction_enabled"] = redaction_enabled  # type: ignore[index]
    retrieve_ctl = Mock()

    with ArtifactCtl(config) as ctl:
        ref = ctl.ingest_bytes(
            b"contact user@example.com card 4242424242424242",
            retrieve_ctl=retrieve_ctl,
        )

        payload = retrieve_ctl.ingest_event.call_args.args[1]
        assert payload["artifact_ref"] == ref.ref
        assert payload["text"] == expected_text
        assert ctl.read_bytes(ref.sha256) == (
            b"contact user@example.com card 4242424242424242"
        )


def test_artifact_ingest_and_retrieve(tmp_path: Path) -> None:
    retrieve_ctl = RetrieveCtl(_retrieve_config(tmp_path))
    try:
        result = retrieve_ctl.ingest_event(
            "artifact.created",
            {
                "artifact_ref": "test-artifact-001",
                "text": "this artifact contains python code for web scraping",
                "scope": "agent:test",
                "title": "scraper.py",
                "tags": ["artifact"],
            },
        )
        assert isinstance(result, dict)
        assert result["source_type"] == "artifact"
        assert int(result["unit_count"]) >= 1

        unit_row = retrieve_ctl.store.execute(
            """
            SELECT COUNT(*) AS count
            FROM retrievectl_units u
            JOIN retrievectl_docs d ON d.doc_id = u.doc_id
            WHERE d.source_type = 'artifact' AND d.source_ref = ?
            """,
            ("test-artifact-001",),
        ).fetchone()
        assert unit_row is not None
        assert int(unit_row["count"]) >= 1

        rows = retrieve_ctl.retrieve(
            query="web scraping code",
            purpose="act",
            scope={"agent": True},
            k=3,
            strategy="contextual",
        )
        assert rows
        assert any(item["ref_type"] == "artifact" for item in rows)
    finally:
        retrieve_ctl.close()
