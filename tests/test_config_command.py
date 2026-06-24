from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock
from argparse import Namespace
from pathlib import Path
import io
import yaml

from openminion.cli.commands import data as data_module
from openminion.cli.commands.config import (
    config_export,
    config_import,
    config_init,
    config_show,
)
from openminion.cli.commands.setup import run_setup


class ConfigCommandTests(unittest.TestCase):
    def test_config_init_defaults_storage_to_config_dir_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "cfg" / "config.json"
            args = Namespace(
                config=str(config_path),
                force=False,
                provider="echo",
                storage_location="config",
                storage_path=None,
            )

            with mock.patch.dict(
                os.environ,
                {
                    "OPENMINION_HOME": "",
                    "OPENMINION_DATA_ROOT": "",
                },
            ):
                code = config_init(args)
            self.assertEqual(code, 0)
            payload = json.loads(config_path.read_text())
            self.assertTrue(payload["runtime"]["demo_mode"])
            self.assertEqual(
                payload["storage"]["path"],
                str(
                    (
                        config_path.parent / ".openminion" / "state" / "openminion.db"
                    ).resolve()
                ),
            )
            self.assertEqual(
                payload["agents"]["openminion"]["default_channel"],
                "console",
            )

    def test_config_init_supports_home_storage_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "cfg" / "config.json"
            args = Namespace(
                config=str(config_path),
                force=False,
                provider="echo",
                storage_location="home",
                storage_path=None,
            )

            code = config_init(args)
            self.assertEqual(code, 0)
            payload = json.loads(config_path.read_text())
            expected = str(
                (Path.home() / ".openminion" / "state" / "openminion.db").resolve()
            )
            self.assertEqual(payload["storage"]["path"], expected)
            self.assertEqual(
                payload["agents"]["openminion"]["default_channel"],
                "console",
            )

    def test_config_init_storage_path_override_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "cfg" / "config.json"
            explicit_storage = Path(tmp) / "db" / "runtime.db"
            args = Namespace(
                config=str(config_path),
                force=False,
                provider="echo",
                storage_location="home",
                storage_path=str(explicit_storage),
            )

            code = config_init(args)
            self.assertEqual(code, 0)
            payload = json.loads(config_path.read_text())
            self.assertEqual(
                payload["storage"]["path"], str(explicit_storage.resolve())
            )

    def test_config_init_non_demo_provider_clears_demo_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "cfg" / "config.json"
            args = Namespace(
                config=str(config_path),
                force=False,
                provider="openai",
                storage_location="config",
                storage_path=None,
            )

            code = config_init(args)
            self.assertEqual(code, 0)
            payload = json.loads(config_path.read_text())
            self.assertFalse(payload["runtime"]["demo_mode"])

    def test_config_show_uses_shared_json_printer_shape(self) -> None:
        args = Namespace(config="ignored.json")
        fake_config = mock.Mock()
        fake_config.to_dict.return_value = {
            "agents": {"openminion": {"provider": "echo"}},
            "runtime": {"demo_mode": True},
        }

        with mock.patch(
            "openminion.cli.commands.config.load_cli_config", return_value=fake_config
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = config_show(args)

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buf.getvalue()), fake_config.to_dict.return_value)

    def test_setup_wizard_writes_cloud_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="ops-agent",
            )

            with mock.patch(
                "builtins.input",
                side_effect=["1", "2", "anthropic-test-key"],
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_setup(args)

            self.assertEqual(code, 0)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["agents"]["ops-agent"]["name"], "ops-agent")
            self.assertEqual(payload["agents"]["ops-agent"]["provider"], "anthropic")
            self.assertFalse(payload["runtime"]["demo_mode"])
            self.assertEqual(
                payload["providers"]["anthropic"]["api_key"], "anthropic-test-key"
            )
            self.assertIn("Initialized onboarding config", buf.getvalue())
            self.assertIn("Stored as a convenience in the config file.", buf.getvalue())
            self.assertIn("it always wins over the config file value", buf.getvalue())

    def test_config_export_strips_secrets_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            export_path = tmp_path / "portable" / "config.export.yaml"
            args = Namespace(
                config=str(config_path),
                force=False,
                provider="openai",
                storage_location="config",
                storage_path=None,
            )
            config_init(args)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["providers"]["openai"]["api_key"] = "stored-secret"
            payload["providers"]["openai"]["api_key_env"] = "OPENAI_API_KEY"
            payload["runtime"]["env"] = {
                "OPENAI_API_KEY": "from-runtime",
                "SAFE_VAR": "kept",
            }
            config_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            export_args = Namespace(
                config=str(config_path),
                output=str(export_path),
                include_secrets=False,
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = config_export(export_args)

            self.assertEqual(code, 0)
            exported_text = export_path.read_text(encoding="utf-8")
            exported = yaml.safe_load(exported_text)
            self.assertNotIn("api_key", exported["providers"]["openai"])
            self.assertNotIn("OPENAI_API_KEY", exported["runtime"]["env"])
            self.assertEqual(exported["runtime"]["env"]["SAFE_VAR"], "kept")
            self.assertIn(
                "# providers.openai.api_key: <stripped — set OPENAI_API_KEY>",
                exported_text,
            )
            self.assertIn("without embedded secrets", buf.getvalue())

    def test_config_import_restores_portable_setup_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "portable.yaml"
            target_path = tmp_path / "cfg" / "config.json"
            input_payload = {
                "agents": {
                    "ops-agent": {
                        "name": "ops-agent",
                        "provider": "openrouter",
                        "system_prompt": "You are OpenMinion, a pragmatic assistant.",
                        "thinking": "minimal",
                        "default_channel": "console",
                    },
                },
                "default_agent": "ops-agent",
                "providers": {
                    "openrouter": {
                        "api_key": "",
                        "api_key_env": "OPENROUTER_API_KEY",
                        "model": "openai/gpt-4.1-mini",
                    }
                },
                "runtime": {
                    "demo_mode": False,
                    "env": {},
                },
                "storage": {
                    "path": str(
                        (tmp_path / ".openminion" / "state" / "openminion.db").resolve()
                    )
                },
            }
            input_path.write_text(
                yaml.safe_dump(input_payload, sort_keys=False), encoding="utf-8"
            )

            import_args = Namespace(
                config=str(target_path),
                input=str(input_path),
                input_flag="",
                force=True,
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = config_import(import_args)

            self.assertEqual(code, 0)
            imported = json.loads(target_path.read_text(encoding="utf-8"))
            self.assertEqual(imported["agents"]["ops-agent"]["provider"], "openrouter")
            self.assertEqual(
                imported["providers"]["openrouter"]["api_key_env"],
                "OPENROUTER_API_KEY",
            )
            self.assertEqual(imported["providers"]["openrouter"]["api_key"], "")
            self.assertIn("override stored values", buf.getvalue())

    def test_config_import_preserves_unrelated_existing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "portable.yaml"
            target_path = tmp_path / "cfg" / "config.json"

            existing_payload = {
                "agents": {
                    "legacy": {
                        "name": "legacy",
                        "provider": "openai",
                    }
                },
                "default_agent": "legacy",
                "providers": {
                    "openai": {
                        "api_key": "",
                        "api_key_env": "OPENAI_API_KEY",
                        "model": "gpt-4.1-mini",
                    }
                },
                "runtime": {"demo_mode": False, "env": {}, "debug_enabled": True},
                "storage": {"path": str((tmp_path / "existing.db").resolve())},
            }
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(
                json.dumps(existing_payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            import_payload = {
                "agents": {
                    "ops-agent": {
                        "name": "ops-agent",
                        "provider": "openrouter",
                    }
                },
                "default_agent": "ops-agent",
                "providers": {
                    "openrouter": {
                        "api_key_env": "OPENROUTER_API_KEY",
                        "model": "openai/gpt-4.1-mini",
                    }
                },
            }
            input_path.write_text(
                yaml.safe_dump(import_payload, sort_keys=False),
                encoding="utf-8",
            )

            import_args = Namespace(
                config=str(target_path),
                input=str(input_path),
                input_flag="",
                force=False,
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
            )
            code = config_import(import_args)

            self.assertEqual(code, 0)
            imported = json.loads(target_path.read_text(encoding="utf-8"))
            self.assertTrue(imported["runtime"]["debug_enabled"])
            self.assertEqual(
                imported["storage"]["path"], str((tmp_path / "existing.db").resolve())
            )
            self.assertIn("openrouter", imported["providers"])

    def test_setup_wizard_writes_ollama_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="ops-agent",
            )

            with mock.patch(
                "builtins.input",
                side_effect=["2", "qwen2.5:14b", "http://localhost:11434"],
            ):
                code = run_setup(args)

            self.assertEqual(code, 0)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["agents"]["ops-agent"]["provider"], "ollama")
            self.assertFalse(payload["runtime"]["demo_mode"])
            self.assertEqual(payload["providers"]["ollama"]["model"], "qwen2.5:14b")
            self.assertEqual(
                payload["providers"]["ollama"]["base_url"], "http://localhost:11434"
            )

    def test_setup_wizard_writes_demo_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="ops-agent",
            )

            with mock.patch("builtins.input", side_effect=["3"]):
                code = run_setup(args)

            self.assertEqual(code, 0)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["agents"]["ops-agent"]["provider"], "echo")
            self.assertTrue(payload["runtime"]["demo_mode"])

    def test_setup_wizard_reprompts_after_invalid_top_level_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="ops-agent",
            )

            with mock.patch("builtins.input", side_effect=["9", "3"]):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_setup(args)

            self.assertEqual(code, 0)
            self.assertIn("Invalid selection. Choose one of: 1, 2, 3.", buf.getvalue())
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["runtime"]["demo_mode"])


class _Report:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return dict(self._payload)


def test_run_data_uses_shared_cli_roots(monkeypatch, tmp_path: Path, capsys) -> None:
    current = sys.modules.get("openminion.cli.commands.data", data_module)

    captured: dict[str, object] = {}
    roots = SimpleNamespace(
        home_root=(tmp_path / "home").resolve(strict=False),
        data_root=(tmp_path / "data").resolve(strict=False),
    )

    monkeypatch.setattr(current, "resolve_cli_roots", lambda **_: roots)

    def _fake_migrate_data_root(*, home_root, data_root, dry_run, logger):
        captured["home_root"] = home_root
        captured["data_root"] = data_root
        captured["dry_run"] = dry_run
        captured["logger_name"] = logger.name
        return _Report(
            {
                "started_at": "start",
                "finished_at": "finish",
                "dry_run": dry_run,
                "items": [],
            }
        )

    monkeypatch.setattr(current, "migrate_data_root", _fake_migrate_data_root)

    args = SimpleNamespace(
        data_command="migrate",
        config=None,
        home_root=None,
        data_root=None,
        dry_run=True,
        json=False,
    )

    assert current.run_data(args) == 0
    assert captured["home_root"] == roots.home_root
    assert captured["data_root"] == roots.data_root
    assert captured["dry_run"] is True
    assert captured["logger_name"] == "openminion.data_migration"
    assert "data migrate report:" in capsys.readouterr().out


def test_run_data_json_output_uses_shared_printer(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    current = sys.modules.get("openminion.cli.commands.data", data_module)

    roots = SimpleNamespace(
        home_root=(tmp_path / "home").resolve(strict=False),
        data_root=(tmp_path / "data").resolve(strict=False),
    )
    payload = {
        "started_at": "start",
        "finished_at": "finish",
        "dry_run": False,
        "items": [{"status": "kept", "source": "a", "target": "b"}],
    }

    monkeypatch.setattr(current, "resolve_cli_roots", lambda **_: roots)
    monkeypatch.setattr(current, "migrate_data_root", lambda **_: _Report(payload))

    args = SimpleNamespace(
        data_command="migrate",
        config=None,
        home_root=None,
        data_root=None,
        dry_run=False,
        json=True,
    )

    assert current.run_data(args) == 0
    assert json.loads(capsys.readouterr().out) == payload
