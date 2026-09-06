from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from openminion.base.config import OpenMinionConfig, load_config, save_config
from openminion.cli.main import main
from openminion.cli.commands.plugins import (
    health_plugin,
    install_plugin,
    preview_plugin,
    rollback_plugin,
    uninstall_plugin,
)


def _write_plugin(root: Path, *, version: str = "1.0.0") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "example.py").write_text(
        "from openminion.services.runtime.plugins import Plugin\n"
        "class ExamplePlugin(Plugin):\n"
        "    name = 'example-plugin'\n",
        encoding="utf-8",
    )
    (root / "example.manifest.json").write_text(
        json.dumps(
            {
                "id": "example.plugin",
                "name": "Example Plugin",
                "version": version,
                "dependencies": ["example-runtime>=1"],
                "config_schema": {"type": "object"},
                "requested_capabilities": ["message.inbound.read"],
                "provenance": {"source": "local-path", "verified": False},
            }
        ),
        encoding="utf-8",
    )


def _args(tmp_path: Path, **values: object) -> Namespace:
    config_path = tmp_path / "config.json"
    if not config_path.exists():
        save_config(OpenMinionConfig(), str(config_path))
    return Namespace(
        config=str(config_path),
        home_root=str(tmp_path),
        data_root=str(tmp_path / "data"),
        root=str(tmp_path / "installed"),
        **values,
    )


def test_plugin_preview_reports_dependencies_permissions_and_provenance(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "source"
    _write_plugin(source)

    assert preview_plugin(_args(tmp_path, source=str(source))) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["plugin"]["dependencies"] == ["example-runtime>=1"]
    assert payload["plugin"]["dependencies_enforced"] is False
    assert payload["plugin"]["config_schema_enforced"] is False
    assert payload["plugin"]["permissions"] == ["message.inbound.read"]
    assert payload["plugin"]["provenance"]["source"] == "local-path"
    assert payload["plugin"]["provenance"]["verification"] == {
        "verified": False,
        "reason_code": "not_claimed",
    }


def test_plugin_preview_reports_malformed_checksum_without_loading_code(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "source"
    _write_plugin(source)
    manifest_path = source / "example.manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["provenance"] = {
        "source": "local-path",
        "verified": True,
        "checksum": "sha256:not-a-digest",
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert preview_plugin(_args(tmp_path, source=str(source))) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["plugin"]["provenance"]["verification"] == {
        "verified": False,
        "reason_code": "checksum_malformed",
    }


def test_plugin_preview_reports_checksum_mismatch_without_loading_code(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "source"
    _write_plugin(source)
    manifest_path = source / "example.manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["provenance"] = {
        "source": "local-path",
        "verified": True,
        "checksum": f"sha256:{'0' * 64}",
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert preview_plugin(_args(tmp_path, source=str(source))) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["plugin"]["provenance"]["verification"] == {
        "verified": False,
        "reason_code": "checksum_mismatch",
    }


def test_plugin_install_health_rollback_and_uninstall(tmp_path: Path, capsys) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_plugin(first, version="1.0.0")
    _write_plugin(second, version="2.0.0")
    install_args = _args(tmp_path, source=str(first))

    assert install_plugin(install_args) == 0
    capsys.readouterr()
    assert "example.plugin" in load_config(install_args.config).enabled_plugins
    assert health_plugin(_args(tmp_path, plugin_id="example.plugin")) == 0
    assert json.loads(capsys.readouterr().out)["healthy"] is True

    assert uninstall_plugin(_args(tmp_path, plugin_id="example.plugin")) == 0
    capsys.readouterr()
    assert "example.plugin" not in load_config(install_args.config).enabled_plugins

    assert install_plugin(_args(tmp_path, source=str(first))) == 0
    capsys.readouterr()
    assert install_plugin(_args(tmp_path, source=str(second))) == 0
    capsys.readouterr()
    assert (
        json.loads((Path(install_args.root) / "example.manifest.json").read_text())[
            "version"
        ]
        == "2.0.0"
    )

    assert rollback_plugin(_args(tmp_path, plugin_id="example.plugin")) == 0
    capsys.readouterr()
    assert (
        json.loads((Path(install_args.root) / "example.manifest.json").read_text())[
            "version"
        ]
        == "1.0.0"
    )


def test_plugin_cli_lifecycle(tmp_path: Path, capsys) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    root = tmp_path / "installed"
    config_path = tmp_path / "config.json"
    _write_plugin(first, version="1.0.0")
    _write_plugin(second, version="2.0.0")
    save_config(OpenMinionConfig(), str(config_path))
    base_args = [
        "--config",
        str(config_path),
        "--home-root",
        str(tmp_path),
        "--data-root",
        str(tmp_path / "data"),
        "plugins",
    ]

    assert main([*base_args, "preview", str(first)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["plugin"]["permissions"] == ["message.inbound.read"]

    assert main([*base_args, "install", str(first), "--root", str(root)]) == 0
    capsys.readouterr()
    assert main([*base_args, "health", "example.plugin", "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["healthy"] is True

    assert main([*base_args, "install", str(second), "--root", str(root)]) == 0
    capsys.readouterr()
    assert main([*base_args, "rollback", "example.plugin", "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "restored"

    assert main([*base_args, "uninstall", "example.plugin", "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "uninstalled"
