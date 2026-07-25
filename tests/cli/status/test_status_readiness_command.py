from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from openminion.base.config import OpenMinionConfig, save_config
from openminion.cli.commands.status import run_status
from openminion.cli.parser.base import build_parser
from tests._csc_fixtures import _csc_install_default_agent


def test_status_readiness_parser_registration() -> None:
    args = build_parser().parse_args(["status", "readiness", "--json"])

    assert args.status_command == "readiness"
    assert args.json is True


def test_status_readiness_json_reports_missing_credentials_without_failing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_openai_config(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "openminion.cli.commands.status.readiness.shutil.which",
        lambda _name: None,
    )

    code = run_status(_args(config_path=config_path, as_json=True))

    payload = json.loads(capsys.readouterr().out)
    checks = {item["id"]: item for item in payload["checks"]}
    assert code == 0
    assert payload["ok"] is True
    assert payload["overall"] == "blocked"
    assert checks["provider"]["status"] == "blocked"
    assert checks["provider"]["safe_next_action"] == "openminion setup"
    assert checks["browser"]["status"] == "available"
    assert checks["gws"]["status"] == "not_configured"
    assert checks["memory"]["status"] == "available"
    assert checks["task_cron"]["status"] == "available"


def test_status_readiness_text_prints_operator_next_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_openai_config(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    code = run_status(_args(config_path=config_path, as_json=False))

    output = capsys.readouterr().out
    assert code == 0
    assert "status readiness: overall=blocked checks=8" in output
    assert "- provider: blocked --" in output
    assert "next: openminion setup" in output
    assert "- browser: available -- Browser-control substrate is installed." in output


def test_status_readiness_missing_config_still_returns_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_config = tmp_path / "missing.json"

    code = run_status(_args(config_path=missing_config, as_json=True))

    payload = json.loads(capsys.readouterr().out)
    checks = {item["id"]: item for item in payload["checks"]}
    assert code == 0
    assert payload["overall"] == "blocked"
    assert checks["provider"]["details"]["state"] == "missing_config"
    assert checks["channels"]["status"] == "blocked"
    assert checks["policy"]["status"] == "blocked"


def _args(*, config_path: Path, as_json: bool) -> Namespace:
    return Namespace(
        config=str(config_path),
        status_command="readiness",
        agent_id=None,
        json=as_json,
    )


def _write_openai_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="openai")  # type: ignore[attr-defined]
    config.runtime.log_level = "ERROR"
    config.storage.path = str(tmp_path / "state" / "openminion.db")
    save_config(config, str(config_path))
    return config_path
