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

from openminion.base.config import AgentProfileConfig, OpenMinionConfig, save_config
from openminion.cli.commands import data as data_module
from openminion.cli.commands.config import (
    config_export,
    config_import,
    config_init,
    config_show,
)
from openminion.cli.commands import setup as setup_command
from openminion.cli.commands.setup import run_setup
from openminion.cli.parser.base import build_parser
from openminion.modules.llm.setup_catalog import get_setup_preset


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

            with (
                mock.patch(
                    "builtins.input",
                    side_effect=[
                        "2",
                        "claude-3-5-sonnet-latest",
                        "y",
                        "y",
                        "n",
                    ],
                ),
                mock.patch(
                    "openminion.cli.commands.setup.getpass",
                    return_value="anthropic-test-key",
                ),
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
            output = buf.getvalue()
            self.assertIn("Configuration saved", output)
            self.assertIn("Setup preview:", output)
            self.assertIn("local config <redacted>", output)
            self.assertIn("service: Anthropic", output)
            self.assertIn("API format: Anthropic Messages API", output)
            self.assertNotIn("runtime adapter:", output)
            self.assertNotIn("shared adapter:", output)
            self.assertNotIn("anthropic-test-key", output)

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

    def test_config_import_force_replaces_instead_of_merging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "portable.yaml"
            target_path = tmp_path / "cfg" / "config.json"
            existing = OpenMinionConfig()
            existing.agents = {
                "legacy": AgentProfileConfig(name="legacy", provider="openai")
            }
            existing.default_agent = "legacy"
            existing.runtime.debug_enabled = True
            save_config(existing, str(target_path), home_root=tmp_path)
            input_path.write_text(
                yaml.safe_dump(
                    {
                        "agents": {
                            "imported": {
                                "name": "imported",
                                "provider": "ollama",
                            }
                        },
                        "default_agent": "imported",
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            code = config_import(
                Namespace(
                    config=str(target_path),
                    input=str(input_path),
                    input_flag="",
                    force=True,
                    home_root=str(tmp_path),
                    data_root=str(tmp_path / ".openminion"),
                )
            )

            self.assertEqual(code, 0)
            imported = json.loads(target_path.read_text(encoding="utf-8"))
            self.assertEqual(set(imported["agents"]), {"imported"})

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
                side_effect=[
                    "5",
                    "qwen2.5:14b",
                    "http://localhost:11434",
                    "y",
                    "n",
                ],
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

    def test_setup_wizard_imports_existing_openminion_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            import_path = tmp_path / "portable.yaml"
            import_path.write_text(
                yaml.safe_dump(
                    {
                        "agents": {
                            "imported-agent": {
                                "name": "imported-agent",
                                "provider": "ollama",
                            }
                        },
                        "default_agent": "imported-agent",
                        "providers": {
                            "ollama": {
                                "model": "qwen2.5:14b",
                                "base_url": "http://127.0.0.1:11434",
                            }
                        },
                        "runtime": {"demo_mode": False},
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="ops-agent",
            )

            with (
                mock.patch(
                    "builtins.input",
                    side_effect=["7", str(import_path), "y"],
                ),
                mock.patch(
                    "openminion.cli.commands.setup._run_setup_doctor",
                    return_value=0,
                ),
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_setup(args)

            self.assertEqual(code, 0)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["default_agent"], "imported-agent")
            self.assertEqual(payload["agents"]["imported-agent"]["provider"], "ollama")
            self.assertFalse(payload["runtime"]["demo_mode"])
            self.assertIn("Import an existing OpenMinion config", buf.getvalue())
            self.assertNotIn("Demo Mode", buf.getvalue())
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)

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

            with mock.patch(
                "builtins.input",
                side_effect=["9", "5", "", "", "y", "n"],
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_setup(args)

            self.assertEqual(code, 0)
            self.assertIn(
                "Invalid selection. Choose one of: 1, 2, 3, 4, 5, 6, 7.",
                buf.getvalue(),
            )
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["agents"]["ops-agent"]["provider"], "ollama")
            self.assertEqual(payload["providers"]["ollama"]["model"], "llama3.1")
            self.assertFalse(payload["runtime"]["demo_mode"])

    def test_setup_wizard_cancels_when_remote_credential_is_missing(self) -> None:
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

            with (
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False),
                mock.patch("builtins.input", side_effect=["1", "", "n"]),
                mock.patch(
                    "openminion.cli.commands.setup.getpass",
                    return_value="",
                ),
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_setup(args)

            self.assertEqual(code, 2)
            self.assertFalse(config_path.exists())
            self.assertIn(
                "Export OPENAI_API_KEY and rerun setup",
                buf.getvalue(),
            )

    def test_setup_wizard_reports_missing_import_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            missing_path = tmp_path / "missing.yaml"
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="ops-agent",
            )

            with mock.patch(
                "builtins.input",
                side_effect=["7", str(missing_path)],
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_setup(args)

            self.assertEqual(code, 2)
            self.assertFalse(config_path.exists())
            self.assertIn("Setup failed: Import file not found at", buf.getvalue())
            self.assertIn(missing_path.name, buf.getvalue())

    def test_setup_wizard_labels_default_model_as_recommended(self) -> None:
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

            with (
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env"}),
                mock.patch("builtins.input", side_effect=["1", "", "y", "n"]),
                mock.patch(
                    "openminion.cli.commands.setup._run_setup_doctor",
                    return_value=0,
                ),
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_setup(args)

            self.assertEqual(code, 0)
            self.assertIn("model: gpt-4.1-mini [recommended]", buf.getvalue())
            self.assertNotIn("Cloud (openai)", buf.getvalue())
            self.assertNotIn("Demo Mode", buf.getvalue())

    def test_setup_wizard_shows_multiple_recommended_models(self) -> None:
        with mock.patch("builtins.input", side_effect=["2"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                model = setup_command._prompt_model(get_setup_preset("minimax"))

        self.assertEqual(model, "MiniMax-M2.7-highspeed")
        output = buf.getvalue()
        self.assertIn("MiniMax-M2.7 (recommended)", output)
        self.assertIn("MiniMax-M2.7-highspeed (recommended)", output)

    def test_setup_wizard_secondary_provider_menu_has_back_and_cancel(self) -> None:
        with mock.patch("builtins.input", side_effect=["6", "b", "6", "c"]):
            buf = io.StringIO()
            with (
                redirect_stdout(buf),
                self.assertRaisesRegex(
                    setup_command.ProviderSetupError,
                    "cancelled before choosing provider",
                ),
            ):
                setup_command._prompt_setup_preset()

        output = buf.getvalue()
        self.assertIn("b. Back", output)
        self.assertIn("c. Cancel setup", output)

    def test_noninteractive_custom_api_format_must_match_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="ops-agent",
                provider="custom-anthropic-compatible",
                model="claude-custom",
                base_url="https://example.invalid/v1",
                api_format="openai-compatible",
                check_provider=False,
            )

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_setup(args)

            self.assertEqual(code, 2)
            self.assertFalse(config_path.exists())
            self.assertIn(
                "requires --provider 'custom-openai-compatible'",
                buf.getvalue(),
            )

    def test_noninteractive_builtin_api_format_must_match_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="ops-agent",
                provider="minimax",
                model="MiniMax-M2.7",
                base_url=None,
                api_format="anthropic-compatible",
                check_provider=False,
            )

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_setup(args)

            self.assertEqual(code, 2)
            self.assertFalse(config_path.exists())
            self.assertIn("does not have an approved", buf.getvalue())

    def test_noninteractive_builtin_api_format_match_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            workspace_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="qwen-agent",
                provider="qwen-dashscope",
                model="qwen3.7-plus",
                base_url=workspace_url,
                api_format="openai-compatible",
                check_provider=False,
            )

            with (
                mock.patch.dict(os.environ, {"DASHSCOPE_API_KEY": "sk-qwen"}),
                mock.patch(
                    "openminion.cli.commands.setup._run_setup_doctor",
                    return_value=0,
                ),
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_setup(args)

            self.assertEqual(code, 0)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["agents"]["qwen-agent"]["provider"], "openai")
            self.assertEqual(payload["providers"]["openai"]["base_url"], workspace_url)
            self.assertEqual(
                payload["providers"]["openai"]["provider_identity"]["service_vendor"],
                "dashscope",
            )

    def test_noninteractive_setup_reports_unsupported_provider_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="ops-agent",
                provider="not-a-provider",
                model="model",
                base_url=None,
                api_format=None,
                check_provider=False,
            )

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_setup(args)

            self.assertEqual(code, 2)
            self.assertFalse(config_path.exists())
            self.assertIn("Setup failed: Unsupported provider preset", buf.getvalue())

    def test_setup_list_providers_is_static_and_discoverable(self) -> None:
        args = Namespace(list_providers=True)

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run_setup(args)

        output = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Supported setup providers:", output)
        self.assertIn("minimax: MiniMax", output)
        self.assertIn("API format: openai-compatible", output)
        self.assertIn("kimi: Kimi / Moonshot AI", output)
        self.assertIn("https://api.moonshot.ai/v1", output)
        self.assertIn("qwen-dashscope: Qwen via DashScope", output)
        self.assertIn("custom-openai-compatible", output)
        self.assertIn("custom-anthropic-compatible", output)
        self.assertNotIn("fixture_verified", output)
        self.assertLessEqual(max(map(len, output.splitlines())), 80)

    def test_setup_parser_does_not_accept_raw_api_key_flag(self) -> None:
        parser = build_parser(selected_command="setup")

        with self.assertRaises(SystemExit):
            parser.parse_args(["setup", "--api-key", "secret"])

    def test_noninteractive_setup_skips_provider_check_without_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="ops-agent",
                provider="openai",
                model="gpt-4.1-mini",
                base_url=None,
                api_format=None,
                check_provider=False,
            )

            with (
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env"}),
                mock.patch(
                    "openminion.cli.commands.setup._run_setup_doctor",
                    return_value=0,
                ),
                mock.patch(
                    "openminion.cli.commands.setup._run_setup_provider_check",
                    return_value=0,
                ) as provider_check,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_setup(args)

            self.assertEqual(code, 0)
            provider_check.assert_not_called()
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["providers"]["openai"]["api_key"], "")
            self.assertEqual(
                payload["providers"]["openai"]["api_key_env"],
                "OPENAI_API_KEY",
            )
            output = buf.getvalue()
            self.assertIn("Connection not tested", output)
            self.assertNotIn("Setup ready", output)
            self.assertNotIn("Setup complete", output)

    def test_noninteractive_setup_runs_provider_check_only_with_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="ops-agent",
                provider="openai",
                model="gpt-4.1-mini",
                base_url=None,
                api_format=None,
                check_provider=True,
            )

            with (
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env"}),
                mock.patch(
                    "openminion.cli.commands.setup._run_setup_doctor",
                    return_value=0,
                ),
                mock.patch(
                    "openminion.cli.commands.setup._run_setup_provider_check",
                    return_value=0,
                ) as provider_check,
            ):
                code = run_setup(args)

            self.assertEqual(code, 0)
            provider_check.assert_called_once_with(config_path=config_path.resolve())

    def test_setup_catches_keyboard_interrupt_before_write(self) -> None:
        args = Namespace(list_providers=False)

        with mock.patch(
            "openminion.cli.commands.setup._run_wizard",
            side_effect=KeyboardInterrupt,
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_setup(args)

        self.assertEqual(code, 130)
        self.assertIn("Setup cancelled; configuration not written.", buf.getvalue())

    def test_setup_treats_explicit_cancellation_as_expected(self) -> None:
        args = Namespace(list_providers=False)

        with mock.patch(
            "openminion.cli.commands.setup._run_wizard",
            side_effect=setup_command.SetupCancelledError(
                "Setup cancelled before writing config."
            ),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_setup(args)

        self.assertEqual(code, 130)
        self.assertEqual(
            buf.getvalue().strip(),
            "Setup cancelled; configuration not written.",
        )

    def test_local_setup_runs_explicit_ollama_check_when_approved(self) -> None:
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

            with (
                mock.patch(
                    "builtins.input",
                    side_effect=[
                        "5",
                        "qwen2.5:14b",
                        "http://localhost:11434",
                        "y",
                        "y",
                    ],
                ),
                mock.patch(
                    "openminion.cli.commands.setup._run_setup_doctor",
                    return_value=0,
                ),
                mock.patch(
                    "openminion.cli.commands.setup._run_setup_provider_check",
                    return_value=1,
                ) as provider_check,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = run_setup(args)

            self.assertEqual(code, 1)
            provider_check.assert_called_once_with(config_path=config_path.resolve())
            self.assertIn("Connection check failed", buf.getvalue())

    def test_minimax_setup_preserves_existing_openai_shared_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "cfg" / "config.json"
            existing = OpenMinionConfig()
            existing.agents = {
                "openai-main": AgentProfileConfig(
                    name="openai-main",
                    provider="openai",
                )
            }
            existing.default_agent = "openai-main"
            existing.providers.openai.api_key_env = "OPENAI_API_KEY"
            existing.providers.openai.model = "gpt-4.1-mini"
            existing.providers.openai.base_url = "https://api.openai.com/v1"
            save_config(existing, str(config_path), home_root=tmp_path)
            args = Namespace(
                config=str(config_path),
                home_root=str(tmp_path),
                data_root=str(tmp_path / ".openminion"),
                no_chat=True,
                agent="minimax-m2-7",
                provider="minimax",
                model="MiniMax-M2.7",
                base_url=None,
                api_format=None,
                check_provider=False,
            )

            with (
                mock.patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-mini"}),
                mock.patch(
                    "openminion.cli.commands.setup._run_setup_doctor",
                    return_value=0,
                ),
            ):
                code = run_setup(args)

            self.assertEqual(code, 0)
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["providers"]["openai"]["api_key_env"],
                "OPENAI_API_KEY",
            )
            overrides = payload["agents"]["minimax-m2-7"]["provider_config_overrides"]
            self.assertEqual(overrides["api_key_env"], "MINIMAX_API_KEY")
            self.assertEqual(overrides["model"], "MiniMax-M2.7")
            self.assertEqual(overrides["base_url"], "https://api.minimax.io/v1")


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
