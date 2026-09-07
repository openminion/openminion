from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from openminion.base.config import OpenMinionConfig, load_config, save_config
from openminion.cli.commands import plugins as plugin_commands
from openminion.cli.main import main
from openminion.cli.commands.plugins import (
    health_plugin,
    install_plugin,
    preview_plugin,
    rollback_plugin,
    uninstall_plugin,
)
from openminion.services.runtime.plugins.discovery import discover_plugin_manifests


def _write_plugin(
    root: Path,
    *,
    version: str = "1.0.0",
    module_alias: str = "example",
    plugin_id: str = "example.plugin",
    config_schema: dict | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{module_alias}.py").write_text(
        "from openminion.services.runtime.plugins import Plugin\n"
        "class ExamplePlugin(Plugin):\n"
        "    name = 'example-plugin'\n",
        encoding="utf-8",
    )
    (root / f"{module_alias}.manifest.json").write_text(
        json.dumps(
            {
                "id": plugin_id,
                "name": "Example Plugin",
                "version": version,
                "dependencies": ["example-runtime>=1"],
                "config_schema": config_schema or {"type": "object"},
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
    payload = {
        "config": str(config_path),
        "home_root": str(tmp_path),
        "data_root": str(tmp_path / "data"),
        "root": str(tmp_path / "installed"),
        **values,
    }
    return Namespace(**payload)


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
    assert payload["plugin"]["bundle_digest"].startswith("sha256:")
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


def test_plugin_preview_reports_only_declared_config_requirements(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "source"
    _write_plugin(
        source,
        config_schema={
            "type": "object",
            "required": ["endpoint", "api_key"],
            "properties": {
                "endpoint": {"type": "string"},
                "api_key": {"type": "string", "writeOnly": True},
                "secret_name_only": {"type": "string"},
            },
        },
    )

    assert preview_plugin(_args(tmp_path, source=str(source))) == 0

    plugin = json.loads(capsys.readouterr().out)["plugin"]
    assert plugin["declared_required_config"] == ["api_key", "endpoint"]
    assert plugin["declared_secret_config"] == ["api_key"]
    assert "secret_name_only" not in plugin["declared_secret_config"]


def test_plugin_install_requires_matching_staged_digest(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source"
    _write_plugin(source)
    preview_plugin(_args(tmp_path, source=str(source)))
    digest = json.loads(capsys.readouterr().out)["plugin"]["bundle_digest"]

    assert (
        install_plugin(_args(tmp_path, source=str(source), expected_digest=digest)) == 0
    )
    capsys.readouterr()
    assert (tmp_path / "installed" / "example.py").exists()

    other_root = tmp_path / "mismatch-installed"
    with pytest.raises(RuntimeError, match="digest mismatch"):
        install_plugin(
            _args(
                tmp_path,
                source=str(source),
                root=str(other_root),
                expected_digest=f"sha256:{'0' * 64}",
            )
        )
    assert not other_root.exists()


def test_plugin_preview_digest_matches_its_staged_snapshot(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _write_plugin(source, version="1.0.0")
    real_copy2 = plugin_commands.shutil.copy2

    def mutate_source_after_manifest_copy(source_path, target_path, *args, **kwargs):
        result = real_copy2(source_path, target_path, *args, **kwargs)
        if Path(source_path).suffix == ".json":
            _write_plugin(source, version="2.0.0")
        return result

    monkeypatch.setattr(
        plugin_commands.shutil,
        "copy2",
        mutate_source_after_manifest_copy,
    )
    preview_plugin(_args(tmp_path, source=str(source)))
    preview = json.loads(capsys.readouterr().out)["plugin"]
    monkeypatch.setattr(plugin_commands.shutil, "copy2", real_copy2)

    assert preview["version"] == "1.0.0"
    with pytest.raises(RuntimeError, match="digest mismatch"):
        install_plugin(
            _args(
                tmp_path,
                source=str(source),
                expected_digest=preview["bundle_digest"],
            )
        )
    assert not (tmp_path / "installed").exists()


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
    assert len(discover_plugin_manifests([Path(install_args.root)])) == 1
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


@pytest.mark.parametrize(
    ("module_alias", "plugin_id", "message"),
    [
        ("renamed", "example.plugin", "alias changes are not supported"),
        ("example", "other.plugin", "belongs to example.plugin"),
    ],
)
def test_plugin_install_rejects_identity_collision_before_mutation(
    tmp_path: Path,
    capsys,
    module_alias: str,
    plugin_id: str,
    message: str,
) -> None:
    first = tmp_path / "first"
    collision = tmp_path / "collision"
    _write_plugin(first, version="1.0.0")
    _write_plugin(
        collision,
        version="2.0.0",
        module_alias=module_alias,
        plugin_id=plugin_id,
    )
    args = _args(tmp_path, source=str(first))
    install_plugin(args)
    capsys.readouterr()
    before = (Path(args.root) / "example.manifest.json").read_bytes()
    enabled_before = load_config(args.config).enabled_plugins

    with pytest.raises(RuntimeError, match=message):
        install_plugin(_args(tmp_path, source=str(collision)))

    assert (Path(args.root) / "example.manifest.json").read_bytes() == before
    assert load_config(args.config).enabled_plugins == enabled_before


def test_plugin_install_rejects_same_identity_at_nested_path(tmp_path: Path) -> None:
    root = tmp_path / "installed"
    source = tmp_path / "source"
    _write_plugin(root / "nested", version="1.0.0")
    _write_plugin(source, version="2.0.0")

    with pytest.raises(RuntimeError, match="already installed at a different path"):
        install_plugin(_args(tmp_path, source=str(source)))

    assert not (root / "example.manifest.json").exists()
    assert not (root / "example.py").exists()


@pytest.mark.parametrize("module_alias", [".", ".."])
def test_plugin_install_rejects_path_alias_without_mutation(
    tmp_path: Path,
    module_alias: str,
) -> None:
    source = tmp_path / "source"
    _write_plugin(
        source,
        module_alias=module_alias,
        plugin_id="example.unsafe",
    )
    args = _args(tmp_path, source=str(source))
    config_before = Path(args.config).read_bytes()

    with pytest.raises(RuntimeError, match="safe module alias"):
        install_plugin(args)

    assert not Path(args.root).exists()
    assert Path(args.config).read_bytes() == config_before


def test_failed_replacement_preserves_previous_rollback(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    third = tmp_path / "third"
    _write_plugin(first, version="1.0.0")
    _write_plugin(second, version="2.0.0")
    _write_plugin(third, version="3.0.0")
    install_plugin(_args(tmp_path, source=str(first)))
    capsys.readouterr()
    install_plugin(_args(tmp_path, source=str(second)))
    capsys.readouterr()

    real_save_state = plugin_commands._save_state
    call_count = 0

    def fail_first_save(root: Path, state: dict) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("state write failed")
        real_save_state(root, state)

    monkeypatch.setattr(plugin_commands, "_save_state", fail_first_save)
    with pytest.raises(OSError, match="state write failed"):
        install_plugin(_args(tmp_path, source=str(third)))
    monkeypatch.setattr(plugin_commands, "_save_state", real_save_state)

    target = tmp_path / "installed" / "example.manifest.json"
    assert json.loads(target.read_text())["version"] == "2.0.0"
    rollback_plugin(_args(tmp_path, plugin_id="example.plugin"))
    capsys.readouterr()
    assert json.loads(target.read_text())["version"] == "1.0.0"


def test_failed_config_write_restores_previous_install(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_plugin(first, version="1.0.0")
    _write_plugin(second, version="2.0.0")
    args = _args(tmp_path, source=str(first))
    install_plugin(args)
    capsys.readouterr()
    state_path = Path(args.root) / ".openminion-plugin-installs.json"
    state_before = state_path.read_bytes()
    enabled_before = load_config(args.config).enabled_plugins
    real_save_config = plugin_commands.save_config
    call_count = 0

    def fail_first_save(config, path: str) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("config write failed")
        real_save_config(config, path)

    monkeypatch.setattr(plugin_commands, "save_config", fail_first_save)
    with pytest.raises(OSError, match="config write failed"):
        install_plugin(_args(tmp_path, source=str(second)))

    target = Path(args.root) / "example.manifest.json"
    assert json.loads(target.read_text())["version"] == "1.0.0"
    assert state_path.read_bytes() == state_before
    assert load_config(args.config).enabled_plugins == enabled_before


def test_failed_target_write_restores_previous_install(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_plugin(first, version="1.0.0")
    _write_plugin(second, version="2.0.0")
    args = _args(tmp_path, source=str(first))
    install_plugin(args)
    capsys.readouterr()
    root = Path(args.root)
    state_path = root / ".openminion-plugin-installs.json"
    state_before = state_path.read_bytes()
    real_copy2 = plugin_commands.shutil.copy2

    def fail_staged_module_copy(source, target, *copy_args, **copy_kwargs):
        if Path(target) == root / "example.py" and Path(source).parent.name.startswith(
            ".openminion-plugin-stage-"
        ):
            raise OSError("target write failed")
        return real_copy2(source, target, *copy_args, **copy_kwargs)

    monkeypatch.setattr(plugin_commands.shutil, "copy2", fail_staged_module_copy)
    with pytest.raises(OSError, match="target write failed"):
        install_plugin(_args(tmp_path, source=str(second)))

    assert (
        json.loads((root / "example.manifest.json").read_text())["version"] == "1.0.0"
    )
    assert state_path.read_bytes() == state_before


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

    assert (
        main(
            [
                *base_args,
                "install",
                str(first),
                "--root",
                str(root),
                "--expected-digest",
                preview["plugin"]["bundle_digest"],
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main([*base_args, "health", "example.plugin", "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["healthy"] is True

    assert main([*base_args, "install", str(second), "--root", str(root)]) == 0
    capsys.readouterr()
    assert main([*base_args, "rollback", "example.plugin", "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "restored"

    assert main([*base_args, "uninstall", "example.plugin", "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "uninstalled"
