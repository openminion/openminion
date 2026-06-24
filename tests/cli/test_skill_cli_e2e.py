from __future__ import annotations

import io
import json
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from openminion.cli.commands.skill import (
    _run_skill_ingest,
    _run_skill_list,
    _run_skill_remove,
    _run_skill_show,
)


def _skill_fixture() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "skills"
        / "cli-chat-smoke"
        / "debug"
        / "SKILL.md"
    )


def _bundle_skill_fixture() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "skill"
        / "fixtures"
        / "external_catalog"
        / "openai"
        / "linear"
        / "SKILL.md"
    )


def _write_skill_config(tmp_path: Path) -> Path:
    payload = {
        "skill": {
            "sqlite_path": "runtime/state/skills.db",
            "wal": False,
            "known_tools": ["file", "run_command", "http_request"],
        }
    }
    path = tmp_path / "skill.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(handler, args: Namespace) -> tuple[int, dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = handler(args)
    out = buf.getvalue().strip()
    return code, json.loads(out) if out else {}


def test_skill_cli_ingest_list_show_remove(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    config_path = _write_skill_config(tmp_path)
    skill_path = _skill_fixture()

    code, payload = _run(
        _run_skill_ingest,
        Namespace(
            file=str(skill_path),
            name=None,
            scope="global",
            agent_id=None,
            config=str(config_path),
        ),
    )
    assert code == 0
    assert payload["ok"] is True
    skill_id = payload["skill_id"]
    version_hash = payload["version_hash"]
    assert skill_id == "cli-chat-smoke-debug"
    assert len(version_hash) == 64

    code, payload = _run(
        _run_skill_list,
        Namespace(
            status=None,
            scope=None,
            agent_id=None,
            tag=None,
            tool=None,
            config=str(config_path),
            json=True,
        ),
    )
    assert code == 0
    assert payload["ok"] is True
    skill_ids = [item["skill_id"] for item in payload["skills"]]
    assert skill_id in skill_ids

    code, payload = _run(
        _run_skill_show,
        Namespace(
            skill_id=skill_id,
            version=None,
            config=str(config_path),
        ),
    )
    assert code == 0
    assert payload["ok"] is True
    skill = payload["skill"]
    assert skill["skill_id"] == skill_id
    assert skill["status"] == "draft"
    assert "file" in skill["tools"]

    code, payload = _run(
        _run_skill_remove,
        Namespace(
            skill_id=skill_id,
            version=None,
            config=str(config_path),
        ),
    )
    assert code == 0
    assert payload["ok"] is True

    code, payload = _run(
        _run_skill_list,
        Namespace(
            status=None,
            scope=None,
            agent_id=None,
            tag=None,
            tool=None,
            config=str(config_path),
            json=True,
        ),
    )
    assert code == 0
    assert payload["ok"] is True
    skill_ids = [item["skill_id"] for item in payload["skills"]]
    assert skill_id not in skill_ids


def test_skill_cli_ingest_invalid_file_type(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    config_path = _write_skill_config(tmp_path)
    bad_path = tmp_path / "bad-skill.txt"
    bad_path.write_text("not markdown", encoding="utf-8")

    code, payload = _run(
        _run_skill_ingest,
        Namespace(
            file=str(bad_path),
            name=None,
            scope="global",
            agent_id=None,
            config=str(config_path),
        ),
    )
    assert code == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_FILE_TYPE"


def test_skill_cli_bundle_skill_uses_descriptor_fields(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    config_path = _write_skill_config(tmp_path)
    skill_path = _bundle_skill_fixture()

    code, payload = _run(
        _run_skill_ingest,
        Namespace(
            file=str(skill_path),
            name=None,
            scope="global",
            agent_id=None,
            config=str(config_path),
        ),
    )
    assert code == 0
    assert payload["ok"] is True
    skill_id = payload["skill_id"]

    code, payload = _run(
        _run_skill_list,
        Namespace(
            status=None,
            scope=None,
            agent_id=None,
            tag=None,
            tool=None,
            config=str(config_path),
            json=True,
        ),
    )
    assert code == 0
    assert payload["ok"] is True
    listed = next(item for item in payload["skills"] if item["skill_id"] == skill_id)
    assert listed["display_name"] == "Linear"
    assert listed["short_description"] == "Manage Linear issues in Codex"
    assert listed["one_liner"] == "Manage Linear issues in Codex"

    code, payload = _run(
        _run_skill_show,
        Namespace(
            skill_id=skill_id,
            version=None,
            config=str(config_path),
        ),
    )
    assert code == 0
    assert payload["ok"] is True
    skill = payload["skill"]
    assert skill["name"] == "linear"
    assert skill["display_name"] == "Linear"
    assert skill["short_description"] == "Manage Linear issues in Codex"
    assert skill["sections"]["summary"] == (
        "Coordinate Linear issue triage, status updates, and ownership changes."
    )


def test_skill_cli_generic_error_keeps_unknown_code(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    config_path = _write_skill_config(tmp_path)
    skill_path = _skill_fixture()

    class _ExplodingSkill:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def ingest_file(self, **_kwargs):
            raise RuntimeError("boom")

        def close(self) -> None:
            return None

    with mock.patch("openminion.cli.commands.skill.Skill", _ExplodingSkill):
        code, payload = _run(
            _run_skill_ingest,
            Namespace(
                file=str(skill_path),
                name=None,
                scope="global",
                agent_id=None,
                config=str(config_path),
            ),
        )

    assert code == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "UNKNOWN"
    assert payload["error"]["message"] == "boom"
