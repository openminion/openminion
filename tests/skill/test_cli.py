from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from openminion.modules.skill.cli import main


DEMO_SKILL_MD = """
---
name: Sync Git Branch
id: git_sync_branch
status: verified
tags: [git, dev]
tools: [tool.shell]
risk: low
applies_to:
  intents: [sync branch, pull latest]
---

## Summary
Pull latest changes for a git branch.

## Procedure
- tool.shell run "git fetch --all"
- tool.shell run "git pull --ff-only"
""".strip()


def _config_path(tmp_path: Path) -> Path:
    db = tmp_path / "skill.db"
    cfg = tmp_path / "skill.json"
    cfg.write_text(
        json.dumps(
            {
                "skill": {
                    "sqlite_path": str(db),
                    "blob_root": str(tmp_path / "blob"),
                    "fallback_root": str(tmp_path / "fallback"),
                    "wal": False,
                }
            }
        ),
        encoding="utf-8",
    )
    return cfg


def _ingest_demo_skill(tmp_path: Path, *, trust: str | None = None) -> tuple[Path, str]:
    md_file = tmp_path / "demo.md"
    md_file.write_text(DEMO_SKILL_MD, encoding="utf-8")
    cfg = _config_path(tmp_path)
    argv = [
        "--config",
        str(cfg),
        "ingest",
        "--name",
        "Sync Git Branch",
        "--file",
        str(md_file),
    ]
    if trust is not None:
        argv.extend(["--trust", trust])
    output = _run_cli(argv)
    payload = json.loads(output)
    assert payload["ok"] is True
    return cfg, payload["skill_id"]


def _run_cli(argv: list[str]) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    assert rc == 0, f"CLI exited non-zero: {rc}; stdout={buf.getvalue()!r}"
    return buf.getvalue()


def _run_cli_expect_failure(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        with pytest.raises(SystemExit) as excinfo:
            main(argv)
    rc = (
        int(excinfo.value.code)
        if isinstance(excinfo.value.code, int)
        else 1
        if excinfo.value.code
        else 0
    )
    return rc, buf.getvalue()


def test_cli_inspect_returns_skill_package(tmp_path: Path) -> None:
    cfg, skill_id = _ingest_demo_skill(tmp_path)
    out = _run_cli(["--config", str(cfg), "inspect", skill_id])
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["skill"]["skill_id"] == skill_id
    assert payload["skill"]["name"] == "Sync Git Branch"


def test_cli_ingest_persists_explicit_trust(tmp_path: Path) -> None:
    cfg, skill_id = _ingest_demo_skill(tmp_path, trust="trusted_local")
    out = _run_cli(["--config", str(cfg), "inspect", skill_id])
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["skill"]["bundle_metadata"]["trust"] == "trusted_local"


def test_cli_disable_sets_status_deprecated(tmp_path: Path) -> None:
    cfg, skill_id = _ingest_demo_skill(tmp_path)
    out = _run_cli(
        [
            "--config",
            str(cfg),
            "disable",
            skill_id,
            "--reason",
            "operator test disable",
        ]
    )
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["disabled"]["skill_id"] == skill_id
    assert payload["disabled"]["new_status"] == "deprecated"
    assert payload["disabled"]["reason"] == "operator test disable"
    deprecated_out = _run_cli(["--config", str(cfg), "list", "--status", "deprecated"])
    deprecated_payload = json.loads(deprecated_out)
    listed_deprecated = {item["skill_id"] for item in deprecated_payload["skills"]}
    assert skill_id in listed_deprecated, (
        "disabled skill should be retrievable via the deprecated status filter"
    )
    active_out = _run_cli(
        ["--config", str(cfg), "list", "--status", "draft,verified,blessed"]
    )
    active_payload = json.loads(active_out)
    active_ids = {item["skill_id"] for item in active_payload["skills"]}
    assert skill_id not in active_ids, (
        "disabled skill should drop out of the active-status filter set"
    )


def test_cli_disable_refuses_without_reason(tmp_path: Path) -> None:
    cfg, skill_id = _ingest_demo_skill(tmp_path)
    # argparse exits via SystemExit(2) on missing required arg; we capture
    # stderr-style failures here.
    with pytest.raises(SystemExit):
        main(["--config", str(cfg), "disable", skill_id])


def test_cli_ingest_rejects_invalid_trust(tmp_path: Path) -> None:
    md_file = tmp_path / "demo.md"
    md_file.write_text(DEMO_SKILL_MD, encoding="utf-8")
    cfg = _config_path(tmp_path)
    with pytest.raises(SystemExit):
        main(
            [
                "--config",
                str(cfg),
                "ingest",
                "--name",
                "Sync Git Branch",
                "--file",
                str(md_file),
                "--trust",
                "mystery",
            ]
        )


def test_cli_remove_dry_run_by_default(tmp_path: Path) -> None:
    cfg, skill_id = _ingest_demo_skill(tmp_path)
    out = _run_cli(
        [
            "--config",
            str(cfg),
            "remove",
            skill_id,
            "--reason",
            "operator test remove dry-run",
        ]
    )
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["would_remove"]["skill_id"] == skill_id
    # The skill is still there.
    list_out = _run_cli(["--config", str(cfg), "list"])
    list_payload = json.loads(list_out)
    listed_ids = {item["skill_id"] for item in list_payload["skills"]}
    assert skill_id in listed_ids


def test_cli_remove_with_apply_actually_deletes(tmp_path: Path) -> None:
    cfg, skill_id = _ingest_demo_skill(tmp_path)
    out = _run_cli(
        [
            "--config",
            str(cfg),
            "remove",
            skill_id,
            "--reason",
            "operator test remove apply",
            "--apply",
        ]
    )
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["dry_run"] is False
    assert payload["removed"]["skill_id"] == skill_id
    assert payload["removed"]["deleted_counts"]["skills"] >= 1
    # And it's gone from list output.
    list_out = _run_cli(["--config", str(cfg), "list"])
    list_payload = json.loads(list_out)
    listed_ids = {item["skill_id"] for item in list_payload["skills"]}
    assert skill_id not in listed_ids


def test_cli_remove_refuses_without_reason(tmp_path: Path) -> None:
    cfg, skill_id = _ingest_demo_skill(tmp_path)
    with pytest.raises(SystemExit):
        main(["--config", str(cfg), "remove", skill_id])


def test_cli_inspect_unknown_skill_emits_error(tmp_path: Path) -> None:
    cfg = _config_path(tmp_path)
    rc, output = _run_cli_expect_failure(
        ["--config", str(cfg), "inspect", "no-such-skill"]
    )
    assert rc == 1
    payload = json.loads(output)
    assert payload["ok"] is False
    assert payload["error"]["code"] in {"NOT_FOUND", "INVALID_ARGUMENT"}
