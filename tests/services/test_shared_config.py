from __future__ import annotations

import os
from pathlib import Path

from openminion.services.config import (
    normalize_memory_capsule_strategy,
    resolve_services_path,
    resolve_services_plugin_paths,
    resolve_services_roots,
)


def test_resolve_services_roots_uses_home_and_relative_data_root(
    tmp_path: Path,
) -> None:
    roots = resolve_services_roots(
        env={
            "OPENMINION_HOME": str(tmp_path),
            "OPENMINION_DATA_ROOT": "runtime-data",
        },
        process_env={},
    )

    assert roots.home_root == tmp_path.resolve(strict=False)
    assert roots.data_root == (tmp_path / "runtime-data").resolve(strict=False)


def test_resolve_services_path_supports_home_and_data_roots(tmp_path: Path) -> None:
    roots = resolve_services_roots(
        env={
            "OPENMINION_HOME": str(tmp_path),
            "OPENMINION_DATA_ROOT": ".openminion",
        },
        process_env={},
    )

    assert resolve_services_path("state/openminion.db", roots=roots) == (
        tmp_path / ".openminion" / "state" / "openminion.db"
    ).resolve(strict=False)
    assert resolve_services_path(
        "config/service-config.yaml",
        roots=roots,
        relative_to="home_root",
    ) == (tmp_path / "config" / "service-config.yaml").resolve(strict=False)


def test_resolve_services_plugin_paths_dedupes_env_entries(tmp_path: Path) -> None:
    first = tmp_path / "plugins-a"
    second = tmp_path / "plugins-b"
    raw = os.pathsep.join([str(first), str(second), str(first)])

    resolved = resolve_services_plugin_paths(
        env={"OPENMINION_PLUGIN_PATHS": raw},
        process_env={},
    )

    assert resolved == [
        first.resolve(strict=False),
        second.resolve(strict=False),
    ]


def test_services_plugin_path_resolution_supports_runtime_callers(
    tmp_path: Path,
) -> None:
    first = tmp_path / "plugins-a"
    second = tmp_path / "plugins-b"
    raw = os.pathsep.join([str(first), str(second)])
    expected = [
        first.resolve(strict=False),
        second.resolve(strict=False),
    ]

    os.environ["OPENMINION_PLUGIN_PATHS"] = raw
    try:
        assert resolve_services_plugin_paths(None) == expected
    finally:
        os.environ.pop("OPENMINION_PLUGIN_PATHS", None)


def test_normalize_memory_capsule_strategy_accepts_shared_aliases() -> None:
    assert normalize_memory_capsule_strategy("snapshot") == "frozen_session"
    assert normalize_memory_capsule_strategy("per_turn") == "dynamic_turn"
    assert normalize_memory_capsule_strategy("write") == "refresh_on_write"
    assert normalize_memory_capsule_strategy("disabled") == "off"
