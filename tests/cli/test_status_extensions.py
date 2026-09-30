from __future__ import annotations

import io
import json
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from openminion.base.config import OpenMinionConfig, save_config
from openminion.cli.commands.status import run_status
from openminion.services.runtime.catalog import ExtensionCatalog
from openminion.services.runtime import lifecycle as lifecycle_module
from tests._csc_fixtures import _csc_install_default_agent


def _write_config(tmp_path: Path) -> Path:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config_path = tmp_path / "config.json"
    save_config(config, str(config_path))
    return config_path


def test_status_extensions_json(tmp_path: Path) -> None:
    args = Namespace(
        config=str(_write_config(tmp_path)),
        status_command="extensions",
        json=True,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = run_status(args)

    assert code == 0
    payload = json.loads(buf.getvalue())
    assert payload["ok"] is True
    assert "catalog" in payload
    assert "providers" in payload
    assert isinstance(payload["tool_providers"], list)
    assert isinstance(payload["plugins"], list)
    assert payload["tool_bootstrap"]
    assert sorted(payload["tool_inventory"]) == ["runtime_only", "unexpected"]
    assert payload["tool_inventory"]["unexpected"] == []


def test_status_extensions_reports_unattempted_tool_provider(tmp_path: Path) -> None:
    entry_point = mock.MagicMock()
    entry_point.name = "sample-fetch"
    entry_point.module = "sample.fetch"
    entry_point.group = "openminion.tool.fetch.providers"
    snapshot = mock.MagicMock()
    snapshot.select.side_effect = lambda **kwargs: (
        [entry_point]
        if kwargs.get("group") == "openminion.tool.fetch.providers"
        else []
    )
    with mock.patch(
        "openminion.services.runtime.catalog.entry_points",
        return_value=snapshot,
    ):
        args = Namespace(
            config=str(_write_config(tmp_path)),
            status_command="extensions",
            json=True,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert run_status(args) == 0

    provider = json.loads(buf.getvalue())["tool_providers"][0]
    assert provider == {
        "family": "fetch",
        "name": "sample-fetch",
        "module": "sample.fetch",
        "group": "openminion.tool.fetch.providers",
        "attempted": False,
        "loaded": False,
        "error": None,
    }


def test_extension_catalog_takes_one_entry_point_snapshot() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    entry_point = mock.MagicMock()
    entry_point.name = "sample-provider"
    entry_point.module = "sample.provider"
    entry_point.group = "llmctl.providers"
    snapshot = mock.MagicMock()
    snapshot.select.side_effect = lambda **kwargs: (
        [entry_point] if kwargs.get("group") == "llmctl.providers" else []
    )

    with mock.patch(
        "openminion.services.runtime.catalog.entry_points",
        return_value=snapshot,
    ) as enumerate_entry_points:
        catalog = ExtensionCatalog.from_config(config)

    assert enumerate_entry_points.call_count == 1
    assert [record.name for record in catalog.providers] == ["sample-provider"]


def test_lifecycle_contract_does_not_enumerate_entry_points(
    monkeypatch,
    tmp_path: Path,
) -> None:
    groups: list[str] = []

    def _count_entry_points(*, group: str):
        groups.append(group)
        return []

    monkeypatch.setattr(
        lifecycle_module,
        "entry_points",
        _count_entry_points,
        raising=False,
    )
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]

    lifecycle_module.LifecycleService(
        config=config,
        home_root=tmp_path,
        data_root=tmp_path,
        catalog=mock.MagicMock(),
    )

    assert groups == []
