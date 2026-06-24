from __future__ import annotations

import argparse
import json
from pathlib import Path

from openminion.cli.commands.mcp import run_mcp


def _args(command: str, **kwargs):
    payload = {"mcp_command": command, "config": kwargs.pop("config", None)}
    payload.update(kwargs)
    return argparse.Namespace(**payload)


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _runtime_config(server: dict[str, object]) -> dict[str, object]:
    return {
        "runtime": {"mcp_servers": [server]},
        "agents": {"default": {"provider": "echo"}},
        "default_agent": "default",
    }


def test_mcp_import_redacts_secret_stdout(tmp_path: Path, capsys) -> None:
    source = _write_json(
        tmp_path / "claude.json",
        {
            "mcpServers": {
                "Fixture": {
                    "command": "node",
                    "args": ["server.js"],
                    "env": {"API_TOKEN": "raw-secret", "SAFE_FLAG": "1"},
                    "env_secret_refs": {"SERVICE_TOKEN": "secret://service/token"},
                    "package_metadata": {
                        "origin": "https://example.invalid/mcp-fixture",
                        "version": "1.2.3",
                        "install_command": ["npm", "install", "fixture"],
                        "trust_state": "trusted",
                    },
                }
            }
        },
    )
    config = tmp_path / "openminion.json"

    assert (
        run_mcp(_args("import", config=str(config), source=str(source), write=True))
        == 0
    )
    output = capsys.readouterr().out
    assert "raw-secret" not in output
    payload = json.loads(output)
    assert payload["imported"][0]["env"]["API_TOKEN"] == "<redacted>"
    assert payload["imported"][0]["env"]["SAFE_FLAG"] == "1"
    assert payload["imported"][0]["env_secret_refs"] == {
        "SERVICE_TOKEN": "secret://service/token"
    }
    assert payload["imported"][0]["package_metadata"]["version"] == "1.2.3"


def test_mcp_list_and_validate_config(tmp_path: Path, capsys) -> None:
    config = _write_json(
        tmp_path / "openminion.json",
        _runtime_config(
            {
                "name": "Fixture",
                "transport": "stdio",
                "command": ["python", "server.py"],
                "trusted": True,
            }
        ),
    )

    assert run_mcp(_args("list", config=str(config))) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["servers"][0]["name"] == "fixture"

    assert run_mcp(_args("validate", config=str(config))) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated == {"issues": [], "ok": True, "server_count": 1}


def test_mcp_validate_reports_untrusted_stdio(tmp_path: Path, capsys) -> None:
    config = _write_json(
        tmp_path / "openminion.json",
        _runtime_config(
            {
                "name": "Fixture",
                "transport": "stdio",
                "command": ["python", "server.py"],
                "stdio_sandbox": {"require_trust": True},
            }
        ),
    )

    assert run_mcp(_args("validate", config=str(config))) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["issues"][0]["reason_code"] == "mcp_stdio_untrusted"
