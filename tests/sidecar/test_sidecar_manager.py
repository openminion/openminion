from __future__ import annotations

from pathlib import Path

from openminion.services.lifecycle.sidecars import (
    SidecarConsent,
    SidecarConsentStore,
    SidecarManager,
    SidecarSpec,
)
from openminion.services.security.policy import (
    SecurityPolicyActor,
    SecurityPolicyEngine,
)


def test_lifecycle_sidecar_surface_is_canonical_runtime_owner() -> None:
    from openminion.services.lifecycle.sidecars import SidecarManager as compatibility
    from openminion.services.runtime.sidecars import SidecarManager as canonical

    assert compatibility is canonical


class FakeAdapter:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self._alive = False

    def status(self) -> dict:
        return {"pid_alive": self._alive, "pid": 123 if self._alive else 0}

    def start(self) -> dict:
        self.start_calls += 1
        self._alive = True
        return {"started": True, "pid": 123}

    def stop(self, *, kill: bool = False) -> dict:
        self.stop_calls += 1
        self._alive = False
        return {"stopped": True, "kill": bool(kill)}


def _build_manager(tmp_path: Path, adapter: FakeAdapter, **kwargs) -> SidecarManager:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    spec = SidecarSpec(
        name="fake",
        description="fake adapter",
        autostart_env_key="FAKE_AUTOSTART",
        prompt="Allow fake sidecar? [y/N]: ",
        adapter=adapter,
    )
    return SidecarManager(
        specs=[spec],
        config_path=str(config_path),
        runtime_env={},
        **kwargs,
    )


def test_consent_store_round_trip(tmp_path: Path) -> None:
    store = SidecarConsentStore(tmp_path / "consent.json")
    consent = SidecarConsent(
        name="fake",
        approved=True,
        approved_at="2026-03-10T00:00:00Z",
        scope="persistent",
    )
    store.set(consent)
    loaded = store.get("fake")
    assert loaded is not None
    assert loaded.approved is True
    assert loaded.scope == "persistent"


def test_manager_start_with_prompt(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    events: list[tuple[str, dict]] = []
    manager = _build_manager(
        tmp_path,
        adapter,
        event_sink=lambda event, payload: events.append((event, payload)),
    )
    result = manager.ensure_started(
        name="fake",
        interactive=True,
        prompt_fn=lambda _: "y",
    )
    assert result["started"] is True
    assert adapter.start_calls == 1
    assert any(event == "sidecar.start.completed" for event, _ in events)


def test_manager_policy_blocks_without_scope(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    policy = SecurityPolicyEngine()
    actor = SecurityPolicyActor(role="external", scopes=frozenset())
    manager = _build_manager(tmp_path, adapter, policy=policy, actor=actor)
    result = manager.ensure_started(name="fake", interactive=False)
    assert result["started"] is False
    assert "policy" in result
