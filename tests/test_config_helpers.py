from types import SimpleNamespace
from pathlib import Path

from openminion.cli.bootstrap.loader import (
    resolve_identity_bundle_root,
    resolve_identity_db_path,
)
from openminion.base.config.runtime import (
    resolve_identity_db_from_env,
    resolve_identity_root_from_env,
)

_HOME = "/tmp/openminion-home"
_DATA_ROOT = "state-data"
_LEGACY_IDENTITY_ROOT = "/tmp/legacy"
_CUSTOM_IDENTITY_ROOT = "/tmp/custom-identity-root"
_CUSTOM_IDENTITY_DB = "/tmp/custom-identity.db"


def test_resolve_identity_bundle_root_prefers_split_field() -> None:
    config = SimpleNamespace(
        identity=SimpleNamespace(bundle_root="/tmp/bundle", root=_LEGACY_IDENTITY_ROOT)
    )
    assert resolve_identity_bundle_root(config) == "/tmp/bundle"


def test_resolve_identity_bundle_root_falls_back_to_legacy_alias() -> None:
    config = {"identity": {"bundle_root": "", "root": _LEGACY_IDENTITY_ROOT}}
    assert resolve_identity_bundle_root(config) == _LEGACY_IDENTITY_ROOT


def test_resolve_identity_db_path_prefers_split_field() -> None:
    config = {"identity": {"db_path": "/tmp/identity.db", "root": "/tmp/legacy.db"}}
    assert resolve_identity_db_path(config) == "/tmp/identity.db"


def test_resolve_identity_db_path_falls_back_to_legacy_alias() -> None:
    config = SimpleNamespace(
        identity=SimpleNamespace(db_path="", root="/tmp/legacy.db")
    )
    assert resolve_identity_db_path(config) == "/tmp/legacy.db"


def test_resolve_identity_root_from_env_defaults_from_home_and_data_root() -> None:
    resolved = resolve_identity_root_from_env(
        env={
            "OPENMINION_HOME": _HOME,
            "OPENMINION_DATA_ROOT": _DATA_ROOT,
        },
        process_env={},
    )
    assert resolved == (Path(_HOME).resolve() / _DATA_ROOT / "identity").resolve()


def test_resolve_identity_root_from_env_prefers_identity_root_override() -> None:
    resolved = resolve_identity_root_from_env(
        env={
            "OPENMINION_HOME": _HOME,
            "OPENMINION_DATA_ROOT": _DATA_ROOT,
            "OPENMINION_IDENTITY_ROOT": _CUSTOM_IDENTITY_ROOT,
        },
        process_env={},
    )
    assert resolved == Path(_CUSTOM_IDENTITY_ROOT).resolve()


def test_resolve_identity_root_from_env_anchors_config_under_data_root() -> None:
    data_root = Path(_HOME) / _DATA_ROOT
    resolved = resolve_identity_root_from_env(
        env={},
        process_env={},
        home_root=Path(_HOME),
        data_root=data_root,
        configured_root="custom-identities",
    )
    assert resolved == (data_root / "custom-identities").resolve()


def test_resolve_identity_db_from_env_defaults_from_identity_root() -> None:
    resolved = resolve_identity_db_from_env(
        env={
            "OPENMINION_HOME": _HOME,
            "OPENMINION_DATA_ROOT": _DATA_ROOT,
        },
        process_env={},
    )
    assert (
        resolved
        == (Path(_HOME).resolve() / _DATA_ROOT / "identity" / "identity.db").resolve()
    )


def test_resolve_identity_db_from_env_prefers_identity_db_override() -> None:
    resolved = resolve_identity_db_from_env(
        env={
            "OPENMINION_HOME": _HOME,
            "OPENMINION_DATA_ROOT": _DATA_ROOT,
            "OPENMINION_IDENTITY_DB": _CUSTOM_IDENTITY_DB,
        },
        process_env={},
    )
    assert resolved == Path(_CUSTOM_IDENTITY_DB).resolve()


def test_resolve_identity_db_from_env_anchors_relative_env_db_under_identity_root() -> (
    None
):
    resolved = resolve_identity_db_from_env(
        env={
            "OPENMINION_HOME": _HOME,
            "OPENMINION_DATA_ROOT": _DATA_ROOT,
            "OPENMINION_IDENTITY_ROOT": _CUSTOM_IDENTITY_ROOT,
            "OPENMINION_IDENTITY_DB": "custom.db",
        },
        process_env={},
    )
    assert resolved == (Path(_CUSTOM_IDENTITY_ROOT) / "custom.db").resolve()


def test_resolve_identity_db_from_env_anchors_config_db_under_data_root() -> None:
    data_root = Path(_HOME) / _DATA_ROOT
    resolved = resolve_identity_db_from_env(
        env={},
        process_env={},
        home_root=Path(_HOME),
        data_root=data_root,
        configured_db="state/custom.db",
        configured_root="custom-identities",
    )
    assert resolved == (data_root / "state" / "custom.db").resolve()
