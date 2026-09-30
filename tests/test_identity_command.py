from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from openminion.cli.commands import identity as identity_command
from openminion.cli.commands import identity_editor
from openminion.modules.identity.runtime.bundle_importer import (
    BundleTextDocument,
    build_profile_from_bundle_documents,
)
from openminion.modules.identity.models import AgentProfile
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage.store import SQLiteIdentityStore


def test_top_level_cli_routes_identity_apply_and_inspect_with_config(
    tmp_path: Path,
) -> None:
    identity_root = tmp_path / "custom-identity"
    identity_db = tmp_path / "custom-state" / "identity.db"
    config_path = tmp_path / "openminion.json"
    config_path.write_text(
        json.dumps(
            {
                "identity": {
                    "bundle_root": str(identity_root),
                    "db_path": str(identity_db),
                }
            }
        ),
        encoding="utf-8",
    )
    profile = build_profile_from_bundle_documents(
        agent_id="ops-agent",
        documents=[
            BundleTextDocument(
                relative_path="AGENT.md",
                content="## Mission\nReach the real top-level CLI.\n",
            ),
            BundleTextDocument(
                relative_path="SOUL.md",
                content="## Voice\n- Precise\n",
            ),
        ],
    )
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(
        yaml.safe_dump(profile.model_dump(mode="python"), sort_keys=False),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.pop("OPENMINION_IDENTITY_ROOT", None)
    env.pop("OPENMINION_IDENTITY_DB", None)
    command = [
        sys.executable,
        "-m",
        "openminion",
        "--config",
        str(config_path),
        "identity",
    ]

    applied = subprocess.run(
        [
            *command,
            "apply",
            "--file",
            str(candidate),
            "--expected-profile-version",
            "missing",
            "--expected-source-sha256",
            "missing",
            "--json",
        ],
        cwd=Path.cwd(),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    inspected = subprocess.run(
        [*command, "inspect", "--agent-id", "ops-agent", "--json"],
        cwd=Path.cwd(),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    listed = subprocess.run(
        [*command, "list"],
        cwd=Path.cwd(),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert applied.returncode == 0, applied.stderr
    assert inspected.returncode == 0, inspected.stderr
    assert listed.returncode == 0, listed.stderr
    assert "ops-agent" in listed.stdout
    assert json.loads(inspected.stdout)["effective_profile"]["role"]["mission"] == (
        "Reach the real top-level CLI."
    )
    assert (identity_root / "ops-agent" / "profile.yaml").is_file()
    assert identity_db.is_file()


def test_run_identity_import_from_bundle_stamps_bundle_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle_root = tmp_path / "bundle-root" / "agents" / "ops-agent"
    bundle_root.mkdir(parents=True)
    (bundle_root / "AGENT.md").write_text(
        "## Mission\nImport from explicit bundle command.\n",
        encoding="utf-8",
    )
    (bundle_root / "SOUL.md").write_text(
        "## Voice\n- Concise\n",
        encoding="utf-8",
    )

    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    identity_command.run_identity_import_from_bundle(str(bundle_root))

    captured = capsys.readouterr()
    assert "imported: ops-agent" in captured.out

    profile = ctl.get_profile("ops-agent")
    assert profile is not None
    if profile is None:  # pragma: no cover
        raise AssertionError("expected imported profile")
    meta = dict(profile.meta or {})
    assert meta.get("source") == "bundle"
    assert bool(meta.get("bundle_imported"))
    assert bool(meta.get("bundle_fingerprint"))


def test_run_identity_import_from_bundle_refuses_yaml_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle_root = tmp_path / "ops-agent"
    bundle_root.mkdir()
    (bundle_root / "AGENT.md").write_text(
        "## Mission\nBundle mission.\n", encoding="utf-8"
    )
    (bundle_root / "SOUL.md").write_text("## Voice\n- Direct\n", encoding="utf-8")

    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    yaml_profile = build_profile_from_bundle_documents(
        agent_id="ops-agent",
        documents=[
            BundleTextDocument(
                relative_path="AGENT.md",
                content="## Mission\nYAML mission.\n",
            )
        ],
    )
    ctl.upsert_profile(yaml_profile.model_copy(update={"meta": {"source": "yaml"}}))
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    with pytest.raises(SystemExit) as exc_info:
        identity_command.run_identity_import_from_bundle(str(bundle_root))

    assert exc_info.value.code == 1
    assert (
        "cannot overwrite a YAML-managed or protected profile"
        in capsys.readouterr().err
    )
    assert ctl.get_profile("ops-agent").role.mission == "YAML mission."


def test_run_identity_import_from_bundle_requires_agent_id_for_identity_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle_root = tmp_path / "bundle-root" / "agents" / "ops-agent"
    bundle_root.mkdir(parents=True)
    (bundle_root / "AGENT.md").write_text(
        "## Mission\nBundle mission\n", encoding="utf-8"
    )
    (bundle_root / "SOUL.md").write_text("## Voice\n- Direct\n", encoding="utf-8")

    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    with pytest.raises(SystemExit):
        identity_command.run_identity_import_from_bundle(str(tmp_path / "bundle-root"))

    captured = capsys.readouterr()
    assert "--agent-id is required" in captured.err


def test_run_identity_export_yaml_single_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    profile = build_profile_from_bundle_documents(
        agent_id="ops-agent",
        documents=[
            BundleTextDocument(
                relative_path="AGENT.md",
                content="## Mission\nKeep service healthy.\n",
            ),
            BundleTextDocument(
                relative_path="SOUL.md",
                content="## Voice\n- Crisp\n",
            ),
        ],
    )
    ctl.upsert_profile(profile)
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    output_path = tmp_path / "single.yaml"
    identity_command.run_identity_export_yaml(str(output_path), agent_id="ops-agent")

    payload = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert payload["agent_id"] == "ops-agent"
    assert payload["role"]["mission"] == "Keep service healthy."
    captured = capsys.readouterr()
    assert "fidelity_notice: YAML export is lossless" in captured.out


def test_run_identity_export_yaml_all_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    ops = build_profile_from_bundle_documents(
        agent_id="ops-agent",
        documents=[
            BundleTextDocument(
                relative_path="AGENT.md",
                content="## Mission\nOps mission\n",
            ),
            BundleTextDocument(
                relative_path="SOUL.md",
                content="## Voice\n- Precise\n",
            ),
        ],
    )
    support = build_profile_from_bundle_documents(
        agent_id="support-agent",
        documents=[
            BundleTextDocument(
                relative_path="AGENT.md",
                content="## Mission\nSupport mission\n",
            ),
            BundleTextDocument(
                relative_path="SOUL.md",
                content="## Voice\n- Friendly\n",
            ),
        ],
    )
    ctl.upsert_profile(ops)
    ctl.upsert_profile(support)
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    output_path = tmp_path / "all.yaml"
    identity_command.run_identity_export_yaml(str(output_path))

    payload = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert sorted(payload["profiles"].keys()) == ["ops-agent", "support-agent"]


def test_run_identity_export_markdown_writes_bundle_and_lockfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    profile = build_profile_from_bundle_documents(
        agent_id="ops-agent",
        documents=[
            BundleTextDocument(
                relative_path="AGENT.md",
                content="## Mission\nShip safe changes.\n",
            ),
            BundleTextDocument(
                relative_path="SOUL.md",
                content="## Voice\n- Clear\n",
            ),
        ],
    )
    ctl.upsert_profile(profile)
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    output_dir = tmp_path / "exports"
    identity_command.run_identity_export(
        output_dir=str(output_dir),
        agent_id="ops-agent",
    )

    bundle_dir = output_dir / "agents" / "ops-agent"
    assert (bundle_dir / "AGENT.md").is_file()
    assert (bundle_dir / "SOUL.md").is_file()
    lockfile = bundle_dir / ".identity-lock.json"
    assert lockfile.is_file()
    payload = json.loads(lockfile.read_text(encoding="utf-8"))
    assert payload["generated_from_profile_version"]
    assert sorted(item["relative_path"] for item in payload["files"]) == [
        "AGENT.md",
        "SOUL.md",
    ]
    captured = capsys.readouterr()
    assert "fidelity_notice: markdown bundle export is lossy" in captured.out


def test_run_identity_diff_reports_semantic_drift_and_lossy_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    base_profile = build_profile_from_bundle_documents(
        agent_id="ops-agent",
        documents=[
            BundleTextDocument(
                relative_path="AGENT.md",
                content="## Mission\nSQLite mission\n",
            ),
            BundleTextDocument(
                relative_path="SOUL.md",
                content="## Voice\n- Crisp\n",
            ),
        ],
    )
    payload = base_profile.model_dump(mode="python")
    payload["role"]["domain"] = ["operations"]
    profile = AgentProfile.model_validate(payload)
    ctl.upsert_profile(profile)
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    bundle_dir = tmp_path / "bundles" / "agents" / "ops-agent"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "AGENT.md").write_text(
        "## Mission\nBundle mission\n",
        encoding="utf-8",
    )
    (bundle_dir / "SOUL.md").write_text(
        "## Voice\n- Crisp\n",
        encoding="utf-8",
    )

    identity_command.run_identity_diff(
        "ops-agent", bundle_dir=str(tmp_path / "bundles")
    )
    captured = capsys.readouterr()
    assert "semantic_bundle_drift_fields:" in captured.out
    assert "role.mission" in captured.out
    assert "fidelity_notice: markdown comparison is lossy" in captured.out
    assert "lossy_fields_not_compared:" in captured.out
    assert "role.domain" in captured.out
    assert "result: drifted" in captured.out


@pytest.mark.parametrize(
    ("drift_kind", "expected_phrase"),
    [
        ("change", "changed files"),
        ("add", "added files"),
        ("remove", "removed files"),
    ],
)
def test_markdown_export_requires_force_when_lockfile_drift_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    drift_kind: str,
    expected_phrase: str,
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    profile = build_profile_from_bundle_documents(
        agent_id="ops-agent",
        documents=[
            BundleTextDocument(
                relative_path="AGENT.md",
                content="## Mission\nStable mission\n",
            ),
            BundleTextDocument(
                relative_path="SOUL.md",
                content="## Voice\n- Stable\n",
            ),
        ],
    )
    ctl.upsert_profile(profile)
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    output_dir = tmp_path / "exports"
    identity_command.run_identity_export(
        output_dir=str(output_dir),
        agent_id="ops-agent",
    )
    bundle_dir = output_dir / "agents" / "ops-agent"

    if drift_kind == "change":
        (bundle_dir / "AGENT.md").write_text(
            "## Mission\nChanged mission\n", encoding="utf-8"
        )
    elif drift_kind == "add":
        (bundle_dir / "NOTES.md").write_text("extra note\n", encoding="utf-8")
    elif drift_kind == "remove":
        (bundle_dir / "SOUL.md").unlink()

    with pytest.raises(SystemExit):
        identity_command.run_identity_export(
            output_dir=str(output_dir),
            agent_id="ops-agent",
        )
    captured = capsys.readouterr()
    assert "without --force" in captured.err
    assert expected_phrase in captured.err

    identity_command.run_identity_export(
        output_dir=str(output_dir),
        agent_id="ops-agent",
        force=True,
    )


def _seed_profile(ctl: IdentityCtl, agent_id: str = "ops-agent") -> AgentProfile:
    profile = build_profile_from_bundle_documents(
        agent_id=agent_id,
        documents=[
            BundleTextDocument(
                relative_path="AGENT.md",
                content=f"## Mission\n{agent_id} mission\n",
            ),
            BundleTextDocument(
                relative_path="SOUL.md",
                content="## Voice\n- Crisp\n",
            ),
        ],
    )
    ctl.upsert_profile(profile)
    return profile


def test_identity_inspect_cli_emits_structured_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    identity_root = tmp_path / "identity"
    source = identity_root / "ops-agent" / "profile.yaml"
    source.parent.mkdir(parents=True)
    profile = _seed_profile(ctl).model_copy(update={"meta": {"source": "yaml"}})
    ctl.upsert_profile(profile)
    source.write_text(
        yaml.safe_dump(profile.model_dump(mode="python", exclude_none=True)),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        identity_editor,
        "get_identity_context",
        lambda **_kwargs: (ctl, identity_root),
    )

    identity_command.run_identity_inspect(agent_id="ops-agent", json_output=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["agent_id"] == "ops-agent"
    assert payload["source"] == "yaml"
    assert payload["authored_yaml"]
    assert payload["render"]["profile_version"] == payload["profile_version"]


def test_identity_validate_file_does_not_open_the_canonical_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile = build_profile_from_bundle_documents(
        agent_id="ops-agent",
        documents=[
            BundleTextDocument(
                relative_path="AGENT.md",
                content="## Mission\nValidate without storage mutation.\n",
            )
        ],
    )
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(
        yaml.safe_dump(profile.model_dump(mode="python", exclude_none=True)),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        identity_command,
        "_get_identityctl",
        lambda: pytest.fail("candidate validation must not open the canonical store"),
    )

    identity_command.run_identity_validate(file_path=str(candidate), json_output=True)

    assert json.loads(capsys.readouterr().out)["agent_id"] == "ops-agent"


def test_identity_apply_cli_writes_and_reports_source_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    identity_root = tmp_path / "identity"
    candidate = tmp_path / "candidate.yaml"
    profile = build_profile_from_bundle_documents(
        agent_id="ops-agent",
        documents=[
            BundleTextDocument(
                relative_path="AGENT.md",
                content="## Mission\nApply through the CLI.\n",
            )
        ],
    )
    candidate.write_text(
        yaml.safe_dump(profile.model_dump(mode="python", exclude_none=True)),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        identity_editor,
        "get_identity_context",
        lambda **_kwargs: (ctl, identity_root),
    )

    identity_command.run_identity_apply(
        str(candidate),
        expected_profile_version="missing",
        expected_source_sha256="missing",
        json_output=True,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["source_sha256"]
    assert payload["verification"]["ok"] is True
    assert (identity_root / "ops-agent" / "profile.yaml").is_file()


def test_identity_delete_rejects_yaml_managed_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    profile = _seed_profile(ctl).model_copy(update={"meta": {"source": "yaml"}})
    ctl.upsert_profile(profile)
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda **_kwargs: ctl)

    with pytest.raises(SystemExit) as exc_info:
        identity_command.run_identity_delete("ops-agent")

    assert exc_info.value.code == 1
    assert "cannot be deleted from SQLite" in capsys.readouterr().err
    assert ctl.get_profile("ops-agent") is not None


# ── IRGR-05: list ──────────────────────────────────────────────────────────


def test_run_identity_list_outputs_seeded_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    _seed_profile(ctl, agent_id="ops-agent")
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda **_kwargs: ctl)

    identity_command.run_identity_list()

    out = capsys.readouterr().out
    assert "Agent ID" in out
    assert "ops-agent" in out


def test_run_identity_list_empty_db_prints_header_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    identity_command.run_identity_list()

    out = capsys.readouterr().out
    assert "Agent ID" in out
    assert "ops-agent" not in out


# ── IRGR-05: show ──────────────────────────────────────────────────────────


def test_run_identity_show_existing_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    _seed_profile(ctl, agent_id="ops-agent")
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    identity_command.run_identity_show("ops-agent")

    out = capsys.readouterr().out
    assert "agent_id: ops-agent" in out
    assert "ops-agent mission" in out


def test_run_identity_show_missing_profile_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    with pytest.raises(SystemExit) as exc_info:
        identity_command.run_identity_show("nonexistent-agent")

    assert exc_info.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_run_identity_validate_outputs_profile_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    _seed_profile(ctl, agent_id="ops-agent")
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda **_kwargs: ctl)

    identity_command.run_identity_validate("ops-agent")

    out = capsys.readouterr().out
    assert "ok: true" in out
    assert "ops-agent" in out


def test_run_identity_validate_missing_profile_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda **_kwargs: ctl)

    with pytest.raises(SystemExit) as exc_info:
        identity_command.run_identity_validate("missing-agent")

    assert exc_info.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_run_identity_warm_cache_and_clear_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    _seed_profile(ctl, agent_id="ops-agent")
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    identity_command.run_identity_warm_cache("ops-agent", purposes=["act"])
    identity_command.run_identity_clear_cache("ops-agent")

    out = capsys.readouterr().out
    assert "warmed: ops-agent (1)" in out
    assert "cleared_cache: ops-agent" in out


# ── IRGR-05: upsert ────────────────────────────────────────────────────────


def test_run_identity_upsert_loads_yaml_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    profile_path = tmp_path / "ops-agent.yaml"
    profile_path.write_text(
        yaml.safe_dump(
            {
                "agent_id": "ops-agent",
                "display_name": "Ops Agent",
                "profile_revision": 1,
                "role": {
                    "mission": "Run ops",
                    "responsibilities": [],
                    "hard_constraints": [],
                },
                "personality": {"tone": "professional", "verbosity": "normal"},
                "risk": {
                    "risk_level": "medium",
                    "confirm_before": ["destructive_actions"],
                },
                "tool_posture": {"tool_use": "allowed"},
            }
        ),
        encoding="utf-8",
    )

    identity_command.run_identity_upsert(str(profile_path))

    out = capsys.readouterr().out
    assert "loaded: ops-agent" in out

    stored = ctl.get_profile("ops-agent")
    assert stored is not None
    if stored is not None:
        assert stored.role.mission == "Run ops"


def test_run_identity_upsert_missing_path_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    with pytest.raises(SystemExit) as exc_info:
        identity_command.run_identity_upsert(str(tmp_path / "missing.yaml"))

    assert exc_info.value.code == 1
    assert "does not exist" in capsys.readouterr().err


# ── IRGR-05: delete ────────────────────────────────────────────────────────


def test_run_identity_delete_existing_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    _seed_profile(ctl, agent_id="ops-agent")
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    identity_command.run_identity_delete("ops-agent")

    out = capsys.readouterr().out
    assert "Successfully deleted" in out
    assert ctl.get_profile("ops-agent") is None


def test_run_identity_delete_missing_profile_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    with pytest.raises(SystemExit) as exc_info:
        identity_command.run_identity_delete("nonexistent-agent")

    assert exc_info.value.code == 1
    assert "not found" in capsys.readouterr().err


# ── IRGR-05: render ────────────────────────────────────────────────────────


def test_run_identity_render_existing_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    _seed_profile(ctl, agent_id="ops-agent")
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    identity_command.run_identity_render("ops-agent", purpose="act", max_tokens=180)

    out = capsys.readouterr().out
    assert "Rendering Stats" in out
    assert "Purpose: act" in out
    assert "Max Tokens: 180" in out


def test_run_identity_render_missing_profile_exits_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(tmp_path / "identity.db"))
    )
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

    with pytest.raises(SystemExit):
        identity_command.run_identity_render(
            "nonexistent-agent", purpose="act", max_tokens=180
        )
