from __future__ import annotations

from openminion.modules.policy import SecurityPolicyEngine
from openminion.services.runtime.catalog import ExtensionCatalog
from openminion.services.runtime.lifecycle import _build_sidecar_manager
from openminion.services.runtime.sidecars import SidecarSpec


class _Adapter:
    def status(self) -> dict[str, object]:
        return {"ok": True}

    def start(self) -> dict[str, object]:
        return {"ok": True}

    def stop(self, *, kill: bool = False) -> dict[str, object]:
        return {"ok": True, "kill": kill}


def test_lifecycle_accepts_process_level_controlplane_sidecar_specs() -> None:
    manager = _build_sidecar_manager(
        catalog=ExtensionCatalog([], [], [], [], sidecars=[]),
        config_path=None,
        runtime_env={},
        policy=SecurityPolicyEngine(),
        agent_id="agent:test",
        logger=__import__("logging").getLogger("test"),
        extra_specs=[
            SidecarSpec(
                name="controlplane-janitor",
                description="janitor",
                autostart_env_key="OPENMINION_CONTROLPLANE_JANITOR_AUTOSTART",
                prompt="start?",
                adapter=_Adapter(),
            )
        ],
    )

    assert manager is not None
    assert manager.list() == ["controlplane-janitor"]
