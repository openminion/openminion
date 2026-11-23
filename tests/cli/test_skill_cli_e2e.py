from __future__ import annotations

import argparse
import io
import json
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

from openminion.cli.commands.skill import (
    _run_skill_admission,
    _run_skill_ingest,
    _run_skill_list,
    _run_skill_remove,
    _run_skill_show,
    register,
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


def _admit_skill(*, skill_id: str, version_hash: str, config: Path) -> None:
    code, payload = _run(
        _run_skill_admission,
        Namespace(
            skill_action="admit",
            skill_id=skill_id,
            version_hash=version_hash,
            expected_active_version_hash="none",
            target_status="verified",
            reason="test fixture admission",
            verification_check=None,
            verification_result=None,
            verification_evidence_ref=None,
            config=str(config),
        ),
    )
    assert code == 0
    assert payload["ok"] is True


def test_project_skill_operator_journey(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    config_path = _write_skill_config(tmp_path)
    repo = tmp_path / "repo-a"
    (repo / ".git").mkdir(parents=True)
    other = tmp_path / "repo-b"
    (other / ".git").mkdir(parents=True)
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="command"))
    ingest_args = parser.parse_args(
        [
            "skill",
            "ingest",
            "--file",
            str(_bundle_skill_fixture()),
            "--scope",
            "project",
            "--repository-root",
            str(repo),
            "--config",
            str(config_path),
        ]
    )
    code, imported = _run(_run_skill_ingest, ingest_args)
    assert code == 0
    assert imported["repository_root"] == str(repo)
    _admit_skill(
        skill_id=imported["skill_id"],
        version_hash=imported["version_hash"],
        config=config_path,
    )
    for root, expected in ((repo, [imported["skill_id"]]), (other, [])):
        args = parser.parse_args(
            [
                "skill",
                "list",
                "--repository-root",
                str(root),
                "--json",
                "--config",
                str(config_path),
            ]
        )
        code, listed = _run(_run_skill_list, args)
        assert code == 0
        assert [row["skill_id"] for row in listed["skills"]] == expected


def test_skill_cli_admit_records_operator_verification_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    config_path = _write_skill_config(tmp_path)
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        "---\nname: evidence-skill\ndescription: Evidence test.\n---\n"
        "\n# Procedure\nInspect the bundle.\n",
        encoding="utf-8",
    )
    code, ingested = _run(
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

    code, admitted = _run(
        _run_skill_admission,
        Namespace(
            skill_action="admit",
            skill_id=ingested["skill_id"],
            version_hash=ingested["version_hash"],
            expected_active_version_hash="none",
            target_status="verified",
            reason="manual review",
            verification_check="bundle review",
            verification_result="passed",
            verification_evidence_ref="review://evidence-skill/v1",
            config=str(config_path),
        ),
    )

    assert code == 0
    assert admitted["verification_check"] == "bundle review"
    assert admitted["verification_result"] == "passed"
    assert admitted["verification_evidence_ref"] == "review://evidence-skill/v1"
    assert admitted["verification_reviewer_id"]


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
    _admit_skill(
        skill_id=skill_id,
        version_hash=version_hash,
        config=config_path,
    )

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
    assert skill["status"] == "verified"
    assert skill["tools"] == ["file.read", "exec.run"]

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
    _admit_skill(
        skill_id=skill_id,
        version_hash=payload["version_hash"],
        config=config_path,
    )

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


def _hello_example() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "skills"
        / "hello"
        / "SKILL.md"
    )


def _list_args(config: Path, *, status: str | None, json_output: bool) -> Namespace:
    return Namespace(
        status=status,
        scope=None,
        agent_id=None,
        tag=None,
        tool=None,
        config=str(config),
        json=json_output,
    )


def test_skill_cli_lists_pending_markdown_skills_until_admission(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    config_path = _write_skill_config(tmp_path)
    other_path = tmp_path / "other" / "SKILL.md"
    other_path.parent.mkdir()
    other_path.write_text(
        _hello_example()
        .read_text(encoding="utf-8")
        .replace("`hello.greet`", "`other.probe`"),
        encoding="utf-8",
    )
    ingested: dict[str, str] = {}
    for path in (_hello_example(), other_path):
        code, payload = _run(
            _run_skill_ingest,
            Namespace(
                file=str(path),
                name=None,
                scope="global",
                agent_id=None,
                config=str(config_path),
            ),
        )
        assert code == 0
        ingested[payload["skill_id"]] = payload["version_hash"]
    assert sorted(ingested) == ["hello.greet", "other.probe"]

    code, listed = _run(
        _run_skill_list, _list_args(config_path, status="draft", json_output=True)
    )
    assert code == 0
    assert listed["skills"] == []
    assert {
        item["skill_id"]: item["version_hash"] for item in listed["pending_versions"]
    } == ingested
    code, listed = _run(
        _run_skill_list, _list_args(config_path, status="verified", json_output=True)
    )
    assert code == 0
    assert listed["pending_versions"] == []

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert (
            _run_skill_list(_list_args(config_path, status=None, json_output=False))
            == 0
        )
    text = buf.getvalue()
    assert "No skills found." not in text
    assert (
        f"hello.greet | SKILL | pending admission | {ingested['hello.greet']}" in text
    )
    assert "see `skill admit --help`" in text

    code, shown = _run(
        _run_skill_show,
        Namespace(skill_id="hello.greet", version=None, config=str(config_path)),
    )
    assert code == 1
    assert shown["error"]["code"] == "NOT_FOUND"
    code, shown = _run(
        _run_skill_show,
        Namespace(
            skill_id="hello.greet",
            version=ingested["hello.greet"],
            config=str(config_path),
        ),
    )
    assert code == 0
    assert shown["skill"]["status"] == "draft"

    code, admitted = _run(
        _run_skill_admission,
        Namespace(
            skill_action="admit",
            skill_id="hello.greet",
            version_hash=ingested["hello.greet"],
            expected_active_version_hash="none",
            target_status="verified",
            reason="reviewed the hello example",
            verification_check="manual review",
            verification_result="passed",
            verification_evidence_ref="review://hello",
            config=str(config_path),
        ),
    )
    assert code == 0
    assert admitted["active_version_hash"] == ingested["hello.greet"]

    code, listed = _run(
        _run_skill_list, _list_args(config_path, status=None, json_output=True)
    )
    assert code == 0
    assert [item["skill_id"] for item in listed["skills"]] == ["hello.greet"]
    assert [item["skill_id"] for item in listed["pending_versions"]] == ["other.probe"]


def test_skill_admit_help_documents_first_admission_sentinel() -> None:
    parser = argparse.ArgumentParser(prog="openminion")
    register(parser.add_subparsers())
    buf = io.StringIO()
    with redirect_stdout(buf), pytest.raises(SystemExit):
        parser.parse_args(["skill", "admit", "--help"])

    help_text = " ".join(buf.getvalue().split())
    assert "use 'none' for the first admission" in help_text
