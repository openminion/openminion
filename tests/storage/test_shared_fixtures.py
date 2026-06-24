from __future__ import annotations

from pathlib import Path


def test_temp_omx_dir_is_empty_directory(temp_omx_dir: Path) -> None:
    assert temp_omx_dir.is_dir()
    assert list(temp_omx_dir.iterdir()) == []


def test_mock_telemetry_hook_records_in_order(mock_telemetry_hook) -> None:
    mock_telemetry_hook.record({"name": "pool.stats", "value": 1})
    mock_telemetry_hook.record({"name": "pool.stats", "value": 2})
    names = [event["name"] for event in mock_telemetry_hook.events]
    values = [event["value"] for event in mock_telemetry_hook.events]
    assert names == ["pool.stats", "pool.stats"]
    assert values == [1, 2]


def test_populated_record_store_seeds_rows(populated_record_store) -> None:
    store = populated_record_store(rows=5)
    rows = store.query_dicts("SELECT id, payload FROM rows ORDER BY id ASC")
    assert len(rows) == 5
    assert rows[0]["payload"] == "row-0"
    assert rows[-1]["payload"] == "row-4"


def test_populated_record_store_default_is_empty(populated_record_store) -> None:
    store = populated_record_store()
    rows = store.query_dicts("SELECT * FROM rows")
    assert rows == []
