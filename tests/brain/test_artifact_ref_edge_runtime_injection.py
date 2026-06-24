from __future__ import annotations

import logging

from openminion.base.config import OpenMinionConfig
from openminion.modules.artifact.models import sha_to_ref
from openminion.modules.brain.adapters.factory import (
    create_memory_adapter,
    create_session_adapter,
)
from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.runtime.bootstrap import build_agent_memory_service
from tests._csc_fixtures import _csc_install_default_agent

_VALID_SHA = "a" * 64
_VALID_REF = f"artifact://sha256/{_VALID_SHA}"


class _RecordingArtifactCtl:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def ref_add(self, owner_type: str, owner_id: str, ref_or_sha: str) -> None:
        self.calls.append(("add", owner_type, owner_id, ref_or_sha))

    def ref_remove(self, owner_type: str, owner_id: str, ref_or_sha: str) -> None:
        self.calls.append(("remove", owner_type, owner_id, ref_or_sha))


class _RuntimeArtifactCtl:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def ingest_bytes(self, **kwargs):
        self.calls.append({str(k): str(v) for k, v in kwargs.items() if v is not None})
        return type(
            "_Ref",
            (),
            {
                "ref": sha_to_ref("b" * 64),
                "sha256": "b" * 64,
            },
        )()


def _memory_enabled_config() -> OpenMinionConfig:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.memory_enabled = True
    config.runtime.memory_provider = "memory_v2"
    return config


def test_create_session_adapter_injects_artifactctl_on_live_factory_path(
    monkeypatch,
    tmp_path,
) -> None:
    from openminion.modules.brain.adapters.factory import (
        artifactctl as artifactctl_module,
    )

    artifactctl = _RecordingArtifactCtl()
    monkeypatch.setattr(
        artifactctl_module,
        "create_default_artifactctl",
        lambda: artifactctl,
    )

    adapter = create_session_adapter(mode="auto", db_path=tmp_path / "sessions.db")

    assert adapter.store._artifactctl is artifactctl  # noqa: SLF001
    session_id = adapter.store.create_session(session_id="sess-ref-edge")
    adapter.append_turn(
        session_id,
        role="user",
        content="hello",
        attachments=[_VALID_REF, "mem://skip"],
    )
    assert artifactctl.calls == [("add", "session", session_id, _VALID_SHA)]


def test_session_adapter_ignores_runtime_local_paths_for_reference_edges(
    monkeypatch,
    tmp_path,
) -> None:
    from openminion.modules.brain.adapters.factory import (
        artifactctl as artifactctl_module,
    )

    artifactctl = _RecordingArtifactCtl()
    monkeypatch.setattr(
        artifactctl_module,
        "create_default_artifactctl",
        lambda: artifactctl,
    )

    adapter = create_session_adapter(mode="auto", db_path=tmp_path / "sessions.db")
    session_id = adapter.store.create_session(session_id="sess-local-path")
    adapter.append_turn(
        session_id,
        role="user",
        content="hello",
        attachments=["artifacts/local-output.txt"],
    )
    assert artifactctl.calls == []


def test_create_memory_adapter_injects_artifactctl_and_filters_non_artifacts(
    monkeypatch,
    tmp_path,
) -> None:
    from openminion.modules.brain.adapters.factory import (
        artifactctl as artifactctl_module,
    )

    artifactctl = _RecordingArtifactCtl()
    monkeypatch.setattr(
        artifactctl_module,
        "create_default_artifactctl",
        lambda: artifactctl,
    )

    adapter = create_memory_adapter(mode="auto", db_path=tmp_path / "memory.db")

    assert adapter.store._artifactctl is artifactctl  # noqa: SLF001
    adapter.put_record(
        scope="session:s1",
        record_type="fact",
        title="Artifact-backed fact",
        content={"text": "alpha"},
        evidence_refs=[_VALID_REF, "mem://skip"],
    )
    assert len(artifactctl.calls) == 1
    op, owner_type, owner_id, target = artifactctl.calls[0]
    assert (op, owner_type, target) == ("add", "memory", _VALID_SHA)
    assert owner_id.startswith("mem_")


def test_runtime_bootstrap_injects_artifactctl_into_memory_store(
    monkeypatch,
    tmp_path,
) -> None:
    from openminion.services import runtime as runtime_pkg
    from openminion.services.runtime import bootstrap as runtime_bootstrap

    del runtime_pkg
    artifactctl = _RecordingArtifactCtl()
    monkeypatch.setattr(
        runtime_bootstrap,
        "create_default_artifactctl",
        lambda: artifactctl,
    )

    adapter = build_agent_memory_service(
        config=_memory_enabled_config(),
        agent_id="artifact-agent",
        memory_root=tmp_path,
        logger=logging.getLogger("artifact-runtime-injection"),
        retrieve_ctl=None,
    )

    assert isinstance(adapter, MemoryServiceGatewayAdapter)

    def _find_artifactctl(obj: object) -> object | None:
        seen: set[int] = set()
        stack = [obj]
        while stack:
            cur = stack.pop()
            if cur is None or id(cur) in seen:
                continue
            seen.add(id(cur))
            direct = getattr(cur, "_artifactctl", None)
            if direct is not None:
                return direct
            for attr in ("_backend", "store", "_store", "_inner"):
                inner = getattr(cur, attr, None)
                if inner is not None:
                    stack.append(inner)
        return None

    resolved_artifactctl = _find_artifactctl(adapter._service._store)  # noqa: SLF001
    assert resolved_artifactctl is artifactctl


def test_tool_adapter_prefers_canonical_refs_for_durable_runtime_artifacts(
    tmp_path,
) -> None:
    artifactctl = _RuntimeArtifactCtl()
    registry = ToolRegistry()

    def _handler(args, ctx):
        artifact = ctx.write_artifact(
            "artifacts/out.txt",
            str(args["payload"]).encode("utf-8"),
            "text/plain",
            durable=True,
        )
        return {"ok": True, "content": "ok", "data": {"artifact": artifact.path}}

    registry.register(
        ToolSpec(
            name="artifact.echo",
            args_model=dict,
            min_scope="READ_ONLY",
            handler=_handler,
        )
    )

    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
        artifactctl=artifactctl,
        policy=Policy(
            raw={
                "workspace_root": str(tmp_path),
                "tools": {
                    "allow_prefix": ["artifact."],
                    "deny_exact": [],
                    "deny_prefix": [],
                },
            }
        ),
    )

    result = adapter.execute(
        command={"tool_name": "artifact.echo", "args": {"payload": "hello"}},
        session_id="sess-artifact-adapter",
        trace_id="trace-artifact-adapter",
    )

    assert result["status"] == "success"
    assert result["artifact_refs"] == [{"ref": sha_to_ref("b" * 64), "role": "output"}]
    assert artifactctl.calls
