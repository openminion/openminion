from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

from pathlib import Path

from openminion.modules.artifact.config import ArtifactCtlConfig
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.artifact.errors import ArtifactCtlError
from openminion.modules.artifact.interfaces import (
    ARTIFACT_INTERFACE_VERSION,
    ensure_artifact_compatibility,
)

from .utils import make_config


def _mock_artifact_config() -> Mock:
    mock_config = Mock(spec=ArtifactCtlConfig)
    mock_config.blob_store = Mock(backend="filesystem_cas")
    mock_config.index = Mock(backend="sqlite", sqlite_path=":memory:", wal=False)
    mock_config.views = Mock(auto_generate=[])
    mock_config.retention = Mock(
        keep_days=30,
        delete_unreferenced_after_days=60,
        purge_grace_days=7,
    )
    mock_config.security = Mock(store_original_path=False, redaction_enabled=False)
    mock_config.aliases = Mock(expire_default_days=30)
    return mock_config


@contextmanager
def _artifact_ctl_with_mock_config():
    mock_config = _mock_artifact_config()
    with (
        patch(
            "openminion.modules.artifact.control.load_config",
            return_value=mock_config,
        ),
        patch("openminion.modules.artifact.control.FileSystemCASBlobStore"),
        patch("openminion.modules.artifact.control.SQLiteArtifactIndex"),
    ):
        yield ArtifactCtl(config=mock_config)


class TestContractVersion:
    def test_contract_version_declared(self):
        with _artifact_ctl_with_mock_config() as instance:
            assert hasattr(instance, "contract_version")
            assert instance.contract_version == ARTIFACT_INTERFACE_VERSION


class TestCompatibilityValidator:
    def test_valid_implementation_passes(self):
        with _artifact_ctl_with_mock_config() as instance:
            success, errors = ensure_artifact_compatibility(instance, strict=False)

        assert success is True
        assert len(errors) == 0

    def test_missing_method_fails(self):

        class BrokenArtifactCtl:
            contract_version = ARTIFACT_INTERFACE_VERSION
            # Missing required methods

        instance = BrokenArtifactCtl()
        success, errors = ensure_artifact_compatibility(instance, strict=False)
        assert success is False
        assert len(errors) > 0
        assert "Missing required method" in str(errors[0])

    def test_version_mismatch_fails(self):

        class WrongVersionArtifactCtl:
            contract_version = "v99"  # Wrong version

        instance = WrongVersionArtifactCtl()
        success, errors = ensure_artifact_compatibility(instance, strict=False)
        assert success is False
        assert len(errors) > 0
        assert "Version mismatch" in str(errors[0])

    def test_strict_mode_raises_error(self):

        class BadArtifactCtl:
            contract_version = "v99"  # Wrong version

        instance = BadArtifactCtl()
        with pytest.raises(Exception):  # ArtifactCtlError will be raised
            ensure_artifact_compatibility(instance, strict=True)


def test_sqlite_index_exposes_hard_delete_methods(tmp_path: Path) -> None:
    with ArtifactCtl(make_config(tmp_path)) as ctl:
        assert callable(getattr(ctl.index, "hard_delete_artifact"))
        assert callable(getattr(ctl.index, "hard_delete_views_for_raw"))


def test_payload_too_large_error_round_trips() -> None:
    err = ArtifactCtlError("PAYLOAD_TOO_LARGE", "too big", {"size_bytes": 10})
    assert err.to_dict()["code"] == "PAYLOAD_TOO_LARGE"
