from __future__ import annotations

import json

import pytest

from openminion.modules.artifact.control import _redact_text
from openminion.modules.artifact.errors import ArtifactCtlError

from .utils import artifact_ctl, read_fixture_bytes, read_fixture_text


def test_json_view_is_cached_and_sorted(tmp_path):
    payload = json.dumps({"b": 2, "a": 1}).encode("utf-8")
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(
            payload, mime="application/json", original_name="data.json"
        )

        first = ctl.ensure_view(ref.sha256, "json")
        second = ctl.ensure_view(ref.sha256, "json")

        assert first.sha256 == second.sha256
        decoded = ctl.read_view(ref.sha256, "json")
        assert list(decoded.keys()) == ["a", "b"]


def test_json_view_rejects_invalid_payload(tmp_path):
    malformed = read_fixture_text("malformed.json").encode("utf-8")
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(malformed, original_name="malformed.json")
        with pytest.raises(ArtifactCtlError) as exc:
            ctl.ensure_view(ref.sha256, "json")
        assert exc.value.code == "UNSUPPORTED_VIEW"


def test_table_view_requires_supported_mime(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(
            b"plain text", mime="text/plain", original_name="notes.txt"
        )
        with pytest.raises(ArtifactCtlError) as exc:
            ctl.ensure_view(ref.sha256, "table")
        assert exc.value.code == "UNSUPPORTED_VIEW"


def test_digest_truncation_and_redaction(tmp_path):
    long_line = "X" * 200
    multi_line = "\n".join([long_line for _ in range(10)])
    large_text = (
        "user@example.com 4242424242424242\n"
        + multi_line
        + "\n"
        + read_fixture_text("large-text.txt")
    )
    overrides = {
        "artifactctl": {"views": {"digest_max_chars": 50, "digest_max_lines": 2}}
    }
    with artifact_ctl(tmp_path, overrides) as ctl:
        ref = ctl.ingest_bytes(large_text.encode("utf-8"), original_name="large.txt")
        digest = ctl.read_digest(ref.sha256)
        assert "truncated_chars" in digest["warnings"]
        assert "truncated_lines" in digest["warnings"]
        assert "[REDACTED_EMAIL]" in digest["excerpt"]
        assert "[REDACTED_NUMBER]" in digest["excerpt"]


def test_text_view_respects_redaction_toggle(tmp_path):
    data = b"contact user@example.com"
    other_data = b"contact user2@example.com"
    overrides = {"artifactctl": {"security": {"redaction_enabled": False}}}
    with artifact_ctl(tmp_path, overrides) as ctl:
        ref = ctl.ingest_bytes(data, original_name="note.txt")
        text = ctl.read_view(ref.sha256, "text")
        assert "user@example.com" in text

    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(other_data, original_name="note2.txt")
        text = ctl.read_view(ref.sha256, "text")
        assert "[REDACTED_EMAIL]" in text


def test_json_view_rejects_large_payload(tmp_path):
    large_json = "{" + ",".join(f'"k{i}":{i}' for i in range(1000)) + "}"
    overrides = {"artifactctl": {"views": {"json_max_chars": 100}}}
    with artifact_ctl(tmp_path, overrides) as ctl:
        ref = ctl.ingest_bytes(large_json.encode("utf-8"), mime="application/json")
        with pytest.raises(ArtifactCtlError) as exc:
            ctl.ensure_view(ref.sha256, "json")
        assert exc.value.code == "VIEW_TOO_LARGE"


def test_table_view_rejects_binary_input_even_with_csv_mime(tmp_path):
    data = read_fixture_bytes("binary-with-null.bin")
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(data, mime="text/csv", original_name="table.csv")
        with pytest.raises(ArtifactCtlError) as exc:
            ctl.ensure_view(ref.sha256, "table")
        assert exc.value.code == "UNSUPPORTED_VIEW"


def test_redaction_preserves_sha256_and_timestamps() -> None:
    sha = "a3f5c9d1e2b47890c1d2e3f4a5b6c7d8e9f0123456789abcdef0123456789ab"
    timestamp = "2026-05-22T15:04:05Z"
    card = "4242424242424242"
    text = f"sha={sha}\ntimestamp={timestamp}\ncard={card}"

    redacted = _redact_text(text)

    assert sha in redacted
    assert timestamp in redacted
    assert card not in redacted
    assert "[REDACTED_NUMBER]" in redacted
