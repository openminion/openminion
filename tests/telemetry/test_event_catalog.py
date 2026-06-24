from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_event_catalog_imports_cleanly() -> None:
    from openminion.modules.telemetry import event_catalog  # noqa: F401


def test_event_types_is_non_empty_frozenset() -> None:
    from openminion.modules.telemetry.events.catalog import EVENT_TYPES

    assert isinstance(EVENT_TYPES, frozenset)
    assert len(EVENT_TYPES) > 0
    # Spot-check representative entries across sections.
    assert "storage.pool.stats" in EVENT_TYPES
    assert "memory.write.completed" in EVENT_TYPES
    assert "llm.call.started" in EVENT_TYPES
    assert "task_plan.declared" in EVENT_TYPES
    assert "component.started" in EVENT_TYPES


def test_register_event_type_returns_input_when_registered() -> None:
    from openminion.modules.telemetry.events.catalog import register_event_type

    assert register_event_type("storage.pool.stats") == "storage.pool.stats"
    # Default mode (strict=False) accepts arbitrary names.
    assert register_event_type("not.in.catalog") == "not.in.catalog"


def test_register_event_type_strict_raises_for_unknown() -> None:
    from openminion.modules.telemetry.events.catalog import (
        UnknownEventTypeError,
        register_event_type,
    )

    with pytest.raises(UnknownEventTypeError):
        register_event_type("definitely.not.in.catalog", strict=True)


def test_register_event_type_strict_passes_for_known() -> None:
    from openminion.modules.telemetry.events.catalog import register_event_type

    assert (
        register_event_type("memory.write.started", strict=True)
        == "memory.write.started"
    )


def test_register_event_type_rejects_empty_input() -> None:
    from openminion.modules.telemetry.events.catalog import (
        UnknownEventTypeError,
        register_event_type,
    )

    with pytest.raises(UnknownEventTypeError):
        register_event_type("")
    with pytest.raises(UnknownEventTypeError):
        register_event_type("   ")


def test_storage_hook_constants_resolve_through_catalog() -> None:
    from openminion.modules.telemetry import event_catalog, storage_hook

    assert storage_hook.POOL_STATS_EVENT_TYPE is event_catalog.STORAGE_POOL_STATS
    assert storage_hook.QUERY_EVENT_TYPE is event_catalog.STORAGE_QUERY
    assert storage_hook.SLOW_QUERY_EVENT_TYPE is event_catalog.STORAGE_SLOW_QUERY
    assert storage_hook.MIGRATION_EVENT_TYPE is event_catalog.STORAGE_MIGRATION


def test_lifecycle_canonical_set_resolves_through_catalog() -> None:
    from openminion.modules.telemetry import event_catalog
    from openminion.modules.telemetry import lifecycle

    assert lifecycle._CANONICAL_EVENT_TYPES is event_catalog.LIFECYCLE_EVENT_TYPES
    assert "component.started" in lifecycle._CANONICAL_EVENT_TYPES


def test_validator_script_reports_ok_against_current_source() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (
        repo_root / "openminion" / "scripts" / "validate/telemetry_event_catalog.py"
    )
    assert script.is_file(), script

    env = {"PYTHONPATH": str(repo_root / "openminion" / "src")}
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, **env},
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["unregistered"] == []
    assert payload["registered"] > 0


def test_validator_script_fails_for_unregistered_literal(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    script = (
        repo_root / "openminion" / "scripts" / "validate/telemetry_event_catalog.py"
    )
    scan_root = tmp_path / "scan-root"
    scan_root.mkdir()
    (scan_root / "bad_event.py").write_text(
        'emit(event_type="definitely.not.in.catalog")\n',
        encoding="utf-8",
    )

    env = {"PYTHONPATH": str(repo_root / "openminion" / "src")}
    result = subprocess.run(
        [sys.executable, str(script), "--scan-root", str(scan_root)],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, **env},
        check=False,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["unregistered"] == ["definitely.not.in.catalog"]
    assert payload["scan_root"] == str(scan_root.resolve(strict=False))
