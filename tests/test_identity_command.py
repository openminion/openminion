from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from openminion.cli.commands import identity as identity_command
from openminion.modules.identity.runtime.bundle_importer import (
    BundleTextDocument,
    build_profile_from_bundle_documents,
)
from openminion.modules.identity.models import AgentProfile
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage.store import SQLiteIdentityStore


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
    monkeypatch.setattr(identity_command, "_get_identityctl", lambda: ctl)

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
