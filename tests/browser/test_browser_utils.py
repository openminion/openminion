from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.tool.runtime.resource_selectors import ResourceSelectors
from openminion.tools.browser.models import TabInfo
from openminion.tools.browser.payloads import (
    extract_instance_id,
    extract_instances,
    extract_tab_id,
    extract_tabs,
    is_meaningful_url,
    is_stale_recoverable_error,
    merge_resource_selectors,
    merge_unique_tuples,
    normalize_path,
)


def _to_tab(row) -> TabInfo:
    return TabInfo(
        id=str(row.get("id") or row.get("tabId") or row.get("tab_id") or ""),
        url=str(row.get("url", "")),
        title=str(row.get("title", "")),
    )


def test_merge_unique_tuples_preserves_order_and_dedupes() -> None:
    out = merge_unique_tuples(("a", "b", "a"), ("b", "c"))
    assert out == ("a", "b", "c")


def test_merge_resource_selectors_merges_with_secondary_precedence() -> None:
    primary = ResourceSelectors(
        paths_read=("/tmp/a",),
        paths_write=("/tmp/w1",),
        args=("--one",),
        cwd="/tmp/primary",
        command="python",
    )
    secondary = ResourceSelectors(
        paths_read=("/tmp/b",),
        paths_write=("/tmp/w1", "/tmp/w2"),
        args=("--two",),
        cwd="/tmp/secondary",
        command="node",
    )

    merged = merge_resource_selectors(primary, secondary)

    assert merged.paths_read == ("/tmp/a", "/tmp/b")
    assert merged.paths_write == ("/tmp/w1", "/tmp/w2")
    assert merged.args == ("--one", "--two")
    assert merged.cwd == "/tmp/secondary"
    assert merged.command == "node"


def test_normalize_path_resolves_inside_base(tmp_path: Path) -> None:
    base = tmp_path / "workspace"
    base.mkdir(parents=True, exist_ok=True)

    out = normalize_path("artifacts/out.txt", str(base))
    assert out == str((base / "artifacts/out.txt").resolve(strict=False))


def test_normalize_path_rejects_escape(tmp_path: Path) -> None:
    base = tmp_path / "workspace"
    base.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.txt"

    with pytest.raises(ValueError):
        normalize_path(str(outside), str(base))


def test_is_meaningful_url_filters_placeholder_tabs() -> None:
    assert is_meaningful_url("https://example.com") is True
    assert is_meaningful_url("about:blank") is False
    assert is_meaningful_url("about:newtab") is False
    assert is_meaningful_url("about:home") is False
    assert is_meaningful_url("chrome://newtab/") is False
    assert is_meaningful_url("edge://newtab/") is False


def test_extract_tabs_and_instances_from_nested_payload() -> None:
    payload = {
        "data": {
            "items": [
                {"id": "tab-1", "url": "https://example.com", "title": "Example"},
                {"id": "", "url": "about:blank", "title": "Blank"},
            ]
        }
    }
    tabs = extract_tabs(payload, to_tab_info=_to_tab)
    assert [tab.id for tab in tabs] == ["tab-1"]

    instance_payload = {
        "result": {
            "items": [
                {"instanceId": "inst-1", "profile": "default", "mode": "headed"},
                {"id": "", "profile": "ignored"},
            ]
        }
    }
    instances = extract_instances(instance_payload)
    assert [instance.id for instance in instances] == ["inst-1"]


def test_extract_ids_and_non_stale_error_negative_path() -> None:
    payload = {"instance": {"id": "inst-1"}, "tab": {"tab_id": "tab-1"}}
    assert extract_instance_id(payload) == "inst-1"
    assert extract_tab_id(payload) == "tab-1"

    # Negative assertion for helper behavior moved out of tool.py
    assert (
        is_stale_recoverable_error(RuntimeError("unexpected internal failure")) is False
    )
