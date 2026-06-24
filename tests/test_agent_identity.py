from pathlib import Path

import pytest

from openminion.services.agent.identity import load_identity_bundle
from openminion.services.identity.client import IdentityBundleClient


def _write_bundle(
    root: Path,
    agent_id: str,
    *,
    agent_text: str = "# Agent\n",
    soul_text: str = "# Soul\n",
    skill_name: str | None = None,
    note_name: str | None = None,
) -> Path:
    bundle_root = root / "agents" / agent_id
    bundle_root.mkdir(parents=True, exist_ok=True)
    (bundle_root / "AGENT.md").write_text(agent_text, encoding="utf-8")
    (bundle_root / "SOUL.md").write_text(soul_text, encoding="utf-8")
    if skill_name is not None:
        skill_root = bundle_root / "SKILLS" / skill_name
        skill_root.mkdir(parents=True, exist_ok=True)
        (skill_root / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    if note_name is not None:
        note_root = bundle_root / "NOTES"
        note_root.mkdir(parents=True, exist_ok=True)
        (note_root / note_name).write_text("# Notes\n", encoding="utf-8")
    return bundle_root


def test_load_identity_bundle_success(tmp_path: Path) -> None:
    _write_bundle(
        tmp_path,
        "ops-agent",
        skill_name="hello",
        note_name="improvements.md",
    )
    bundle = load_identity_bundle("ops-agent", root=tmp_path)
    assert bundle.ok is True
    assert bundle.agent_id == "ops-agent"
    assert bundle.agent is not None
    assert bundle.soul is not None
    assert len(bundle.skills) == 1
    assert len(bundle.notes) == 1
    assert len(bundle.errors) == 0
    assert bundle.fingerprint


def test_load_identity_bundle_missing_required_files(tmp_path: Path) -> None:
    bundle_root = tmp_path / "agents" / "ops-agent"
    (bundle_root / "SKILLS").mkdir(parents=True)
    bundle = load_identity_bundle("ops-agent", root=tmp_path)
    assert bundle.ok is False
    assert len(bundle.errors) >= 2
    assert "missing required identity file: AGENT.md" in bundle.errors
    assert "missing required identity file: SOUL.md" in bundle.errors


def test_load_identity_bundle_fingerprint_is_stable(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "ops-agent", skill_name="hello")
    first = load_identity_bundle("ops-agent", root=tmp_path)
    second = load_identity_bundle("ops-agent", root=tmp_path)
    assert first.fingerprint == second.fingerprint


def test_identity_bundle_client_renders_bundle(tmp_path: Path) -> None:
    _write_bundle(
        tmp_path,
        "test-agent",
        agent_text="# Test Agent\n",
        soul_text="# Test Soul\n",
    )
    client = IdentityBundleClient(agent_id="test-agent", root=tmp_path)
    snippet = client.render(agent_id="test-agent", purpose="act", max_tokens=200)
    assert snippet.agent_id == "test-agent"
    assert snippet.purpose == "act"
    assert "# Test Agent" in snippet.text
    assert "# Test Soul" in snippet.text
    assert snippet.profile_version.startswith("bundle:")
    assert snippet.render_version == "v1:real"


def test_identity_bundle_client_fallback_on_missing_bundle(tmp_path: Path) -> None:
    client = IdentityBundleClient(agent_id="nonexistent-agent", root=tmp_path)
    snippet = client.render(agent_id="nonexistent-agent", purpose="act", max_tokens=200)
    assert snippet.agent_id == "nonexistent-agent"
    assert "fallback" in snippet.text
    assert snippet.profile_version == "fallback:v1"


def test_identity_bundle_client_bundle_ok_property(tmp_path: Path) -> None:
    client_missing = IdentityBundleClient(agent_id="valid-agent", root=tmp_path)
    assert client_missing.bundle_ok is False
    _write_bundle(tmp_path, "valid-agent")
    client_present = IdentityBundleClient(agent_id="valid-agent", root=tmp_path)
    assert client_present.bundle_ok is True


def test_identity_bundle_client_fingerprint(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "fingerprint-test")
    client = IdentityBundleClient(agent_id="fingerprint-test", root=tmp_path)
    fingerprint = client.fingerprint
    assert isinstance(fingerprint, str)
    assert len(fingerprint) == 64


def test_identity_bundle_client_root_path(tmp_path: Path) -> None:
    _write_bundle(tmp_path, "path-test")
    client = IdentityBundleClient(agent_id="path-test", root=tmp_path)
    assert "path-test" in client.root_path


def test_bundle_present_used_in_context_path(tmp_path: Path) -> None:
    _write_bundle(
        tmp_path,
        "context-test",
        agent_text="# Context Agent\n",
        soul_text="# Context Soul\n",
    )
    bundle = load_identity_bundle("context-test", root=tmp_path)
    assert bundle.ok is True
    client = IdentityBundleClient(agent_id="context-test", root=tmp_path)
    snippet = client.render(agent_id="context-test", purpose="act", max_tokens=200)
    assert "fallback" not in snippet.text
    assert snippet.profile_version.startswith("bundle:")


def test_missing_bundle_strict_mode_fail_fast(tmp_path: Path) -> None:
    bundle = load_identity_bundle("missing-agent", root=tmp_path)
    assert bundle.ok is False
    assert "identity bundle root not found" in bundle.errors[0]


def test_fail_open_explicit_fallback_events(tmp_path: Path) -> None:
    client = IdentityBundleClient(agent_id="missing-agent", root=tmp_path)
    snippet = client.render(agent_id="missing-agent", purpose="act", max_tokens=200)
    assert "fallback" in snippet.text
    assert snippet.profile_version == "fallback:v1"
    assert snippet.render_version == "fallback:v1"


def test_load_identity_bundle_defaults_to_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home_root = tmp_path / "home"
    data_root = home_root / ".openminion"
    bundle_root = _write_bundle(
        data_root,
        "default-agent",
        agent_text="# Default Agent\n",
        soul_text="# Default Soul\n",
    )
    monkeypatch.setenv("OPENMINION_HOME", str(home_root))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)
    bundle = load_identity_bundle("default-agent")
    assert bundle.ok
    assert Path(bundle.root_path) == bundle_root
