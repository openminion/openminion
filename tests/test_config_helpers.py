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


def test_resolve_identity_bundle_root_prefers_split_field() -> None:
    config = SimpleNamespace(
        identity=SimpleNamespace(bundle_root="/tmp/bundle", root="/tmp/legacy")
    )
    assert resolve_identity_bundle_root(config) == "/tmp/bundle"


def test_resolve_identity_bundle_root_falls_back_to_legacy_alias() -> None:
    config = {"identity": {"bundle_root": "", "root": "/tmp/legacy"}}
    assert resolve_identity_bundle_root(config) == "/tmp/legacy"


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
            "OPENMINION_HOME": "/tmp/openminion-home",
            "OPENMINION_DATA_ROOT": "state-data",
        },
        process_env={},
    )
    assert (
        resolved
        == (
            Path("/tmp/openminion-home").resolve() / "state-data" / "identity"
        ).resolve()
    )


def test_resolve_identity_root_from_env_prefers_identity_root_override() -> None:
    resolved = resolve_identity_root_from_env(
        env={
            "OPENMINION_HOME": "/tmp/openminion-home",
            "OPENMINION_DATA_ROOT": "state-data",
            "OPENMINION_IDENTITY_ROOT": "/tmp/custom-identity-root",
        },
        process_env={},
    )
    assert resolved == Path("/tmp/custom-identity-root").resolve()


def test_resolve_identity_db_from_env_defaults_from_identity_root() -> None:
    resolved = resolve_identity_db_from_env(
        env={
            "OPENMINION_HOME": "/tmp/openminion-home",
            "OPENMINION_DATA_ROOT": "state-data",
        },
        process_env={},
    )
    assert (
        resolved
        == (
            Path("/tmp/openminion-home").resolve()
            / "state-data"
            / "identity"
            / "identity.db"
        ).resolve()
    )


def test_resolve_identity_db_from_env_prefers_identity_db_override() -> None:
    resolved = resolve_identity_db_from_env(
        env={
            "OPENMINION_HOME": "/tmp/openminion-home",
            "OPENMINION_DATA_ROOT": "state-data",
            "OPENMINION_IDENTITY_DB": "/tmp/custom-identity.db",
        },
        process_env={},
    )
    assert resolved == Path("/tmp/custom-identity.db").resolve()
