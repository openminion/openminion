from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.memory.portability import (
    MemoryBundle,
    export_bundle,
    import_bundle,
    load_bundle,
    save_bundle,
)


def _sample_items() -> list[dict]:
    return [
        {"id": "m1", "kind": "fact", "text": "user prefers concise replies"},
        {"id": "m2", "kind": "fact", "text": "user is on Python 3.11"},
    ]


def test_export_bundle_round_trip_through_json() -> None:
    bundle = export_bundle(_sample_items(), metadata={"source": "test"})
    text = bundle.to_json()
    restored = MemoryBundle.from_json(text)
    assert restored.items == bundle.items
    assert restored.metadata == bundle.metadata


def test_save_and_load_bundle_round_trip(tmp_path: Path) -> None:
    bundle = export_bundle(_sample_items())
    path = tmp_path / "bundle.json"
    save_bundle(bundle, path)
    loaded = load_bundle(path)
    assert loaded.items == bundle.items


def test_import_bundle_returns_planning_summary() -> None:
    bundle = export_bundle(_sample_items(), metadata={"agent": "primary"})
    summary = import_bundle(bundle, trust_mode="direct")
    assert summary == {
        "trust_mode": "direct",
        "bundle_version": bundle.version,
        "item_count": 2,
        "metadata": {"agent": "primary"},
    }


def test_import_bundle_candidate_mode_supported() -> None:
    bundle = export_bundle(_sample_items())
    summary = import_bundle(bundle, trust_mode="candidate")
    assert summary["trust_mode"] == "candidate"


def test_import_bundle_rejects_unknown_trust_mode() -> None:
    bundle = export_bundle(_sample_items())
    with pytest.raises(InvalidArgumentError, match="trust_mode"):
        import_bundle(bundle, trust_mode="unsafe")


def test_memory_bundle_default_version_is_internal_codec_version() -> None:
    from openminion.modules.memory.portability import MEMORY_BUNDLE_VERSION

    bundle = MemoryBundle()
    assert bundle.version == MEMORY_BUNDLE_VERSION


def test_memory_bundle_from_json_tolerates_missing_optional_fields() -> None:
    bundle = MemoryBundle.from_json('{"items": [{"id": "x"}]}')
    assert bundle.items == [{"id": "x"}]
    assert bundle.metadata == {}
