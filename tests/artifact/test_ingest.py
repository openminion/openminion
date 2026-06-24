from __future__ import annotations

import logging
from pathlib import Path

import pytest

from openminion.modules.artifact.control import _determine_mime
from openminion.modules.artifact.errors import ArtifactCtlError

from .utils import artifact_ctl, read_fixture_bytes


def test_ingest_file_deduplicates_and_preserves_metadata(tmp_path: Path) -> None:
    src = tmp_path / "sample.txt"
    src.write_text("hello world", encoding="utf-8")

    with artifact_ctl(tmp_path) as ctl:
        first = ctl.ingest_file(src)
        second = ctl.ingest_file(src)

        assert first.sha256 == second.sha256
        meta = ctl.get(first.sha256)
        assert meta.original_name == "sample.txt"


def test_ingest_bytes_respects_store_original_path(tmp_path: Path) -> None:
    overrides = {"artifactctl": {"security": {"store_original_path": True}}}
    with artifact_ctl(tmp_path, overrides) as ctl:
        ref = ctl.ingest_bytes(b"payload", original_name="bytes.bin")
        meta = ctl.get(ref.sha256)
        assert meta.original_path.endswith("bytes.bin")


def test_ingest_bytes_detects_binary_and_mime(tmp_path: Path) -> None:
    data = read_fixture_bytes("binary-with-null.bin")
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(data, original_name="bin.dat")
        meta = ctl.get(ref.sha256)
        assert meta.mime == "application/octet-stream"


def test_duplicate_ingest_preserves_first_metadata(tmp_path: Path) -> None:
    with artifact_ctl(tmp_path) as ctl:
        payload = b"hello metadata"
        first = ctl.ingest_bytes(payload, original_name="first.txt", label="initial")
        ctl.ingest_bytes(payload, original_name="second.txt", label="new-label")

        meta = ctl.get(first.sha256)
        assert meta.original_name == "first.txt"
        assert meta.label == "initial"


def test_store_original_path_disabled_by_default(tmp_path: Path) -> None:
    src = tmp_path / "local.txt"
    src.write_text("content", encoding="utf-8")
    with artifact_ctl(tmp_path) as ctl:
        meta = ctl.get(ctl.ingest_file(src).sha256)
        assert meta.original_path is None


def test_ingest_file_infers_json_mime(tmp_path: Path) -> None:
    src = tmp_path / "data.json"
    src.write_text("{a:1}", encoding="utf-8")
    with artifact_ctl(tmp_path) as ctl:
        meta = ctl.get(ctl.ingest_file(src).sha256)
        assert meta.mime == "application/json"


def test_mime_detection_edge_cases(tmp_path: Path) -> None:
    src = tmp_path / "data.yaml"
    src.write_text("a: 1\n", encoding="utf-8")

    assert (
        _determine_mime(provided=None, path=None, sample=b'  {"a": 1}')
        == "application/json"
    )
    assert _determine_mime(provided=None, path=src, sample=src.read_bytes()) in {
        "text/yaml",
        "application/x-yaml",
    }
    assert (
        _determine_mime(provided=None, path=None, sample=b'\xef\xbb\xbf{"a": 1}')
        == "application/json"
    )


def test_ingest_rejects_oversized_payload(tmp_path: Path) -> None:
    overrides = {"artifactctl": {"blob_store": {"max_ingest_bytes": 100}}}
    with artifact_ctl(tmp_path, overrides) as ctl:
        with pytest.raises(ArtifactCtlError) as exc:
            ctl.ingest_bytes(b"x" * 200, original_name="large.bin")

    assert exc.value.code == "PAYLOAD_TOO_LARGE"


def test_ingest_sets_encoding_field(tmp_path: Path) -> None:
    with artifact_ctl(tmp_path) as ctl:
        text_ref = ctl.ingest_bytes("hello".encode("utf-8"), original_name="text.txt")
        binary_ref = ctl.ingest_bytes(b"\x00\xff\x01", original_name="bin.dat")
        empty_ref = ctl.ingest_bytes(b"", original_name="empty.txt")

        assert ctl.get(text_ref.sha256).encoding == "utf-8"
        assert ctl.get(binary_ref.sha256).encoding == "binary"
        assert ctl.get(empty_ref.sha256).encoding is None


def test_ingest_logs_unexpected_view_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with artifact_ctl(tmp_path) as ctl:
        caplog.set_level(logging.WARNING)

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(ctl, "ensure_view", _boom)
        ctl.ingest_bytes(b"hello", original_name="hello.txt")

    assert "view generation failed for" in caplog.text
    assert "boom" in caplog.text
