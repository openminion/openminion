from __future__ import annotations

from pathlib import Path

import yaml

from openminion.modules.registry.models import AgentDescriptor, ResolveConstraints
from openminion.modules.registry.agents import AgentRegistry
from openminion.modules.registry.storage.store import SQLiteRegistryStore


def _write_manifest(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _descriptor(
    agent_id: str,
    *,
    transport: str,
    address: str,
    priority: int,
    quality: str = "standard",
) -> dict:
    return {
        "agent_id": agent_id,
        "display_name": agent_id,
        "version": "0.0.1",
        "tags": ["validator"],
        "capabilities": [
            {
                "name": "validate",
                "methods": ["validate.factcheck"],
                "quality_tier": quality,
                "cost_tier": "standard",
            }
        ],
        "endpoints": [
            {
                "endpoint_id": "default",
                "transport": transport,
                "address": address,
                "priority": priority,
                "enabled": True,
            }
        ],
        "auth": {"mode": "none"},
    }


def test_load_manifest_and_method_index(tmp_path: Path) -> None:
    manifest = tmp_path / "agents.yaml"
    store_path = tmp_path / "registry.db"

    _write_manifest(
        manifest,
        {
            "schema_version": 1,
            "agents": [
                _descriptor(
                    "validator-1",
                    transport="inproc",
                    address="entrypoint:demo.validator:handle",
                    priority=0,
                    quality="high",
                ),
                _descriptor(
                    "summarizer-1",
                    transport="http",
                    address="http://127.0.0.1:8081",
                    priority=5,
                    quality="standard",
                ),
            ],
        },
    )

    store = SQLiteRegistryStore(store_path, wal=False)
    registry = AgentRegistry(manifest_path=manifest, store=store)
    try:
        registry.load()

        rows = registry.list()
        assert len(rows) == 2

        validator = registry.get("validator-1")
        assert validator is not None
        assert validator.display_name == "validator-1"

        by_method = registry.find_by_method("validate.factcheck")
        assert [row.agent_id for row in by_method] == ["summarizer-1", "validator-1"]

        indexed_ids = store.find_agent_ids_by_method("validate.factcheck")
        assert indexed_ids == ["summarizer-1", "validator-1"]
    finally:
        registry.close()


def test_runtime_overlay_overrides_manifest_on_reload(tmp_path: Path) -> None:
    manifest = tmp_path / "agents.yaml"
    store_path = tmp_path / "registry.db"

    _write_manifest(
        manifest,
        {
            "schema_version": 1,
            "agents": [
                _descriptor(
                    "validator-1",
                    transport="http",
                    address="http://127.0.0.1:8000",
                    priority=10,
                )
            ],
        },
    )

    store = SQLiteRegistryStore(store_path, wal=False)
    registry = AgentRegistry(
        manifest_path=manifest, store=store, allow_runtime_override=True
    )
    try:
        registry.load()

        runtime_desc = AgentDescriptor.model_validate(
            _descriptor(
                "validator-1",
                transport="inproc",
                address="entrypoint:runtime.validator:handle",
                priority=0,
                quality="high",
            )
        )
        registry.register(runtime_desc, source="runtime")

        registry.reload()

        resolved = registry.get("validator-1")
        assert resolved is not None
        assert resolved.endpoints[0].transport == "inproc"
        assert resolved.endpoints[0].address == "entrypoint:runtime.validator:handle"

        record = store.get_agent_record("validator-1")
        assert record is not None
        assert record.source == "runtime"
    finally:
        registry.close()


def test_resolve_is_status_aware_then_priority_and_preference(tmp_path: Path) -> None:
    manifest = tmp_path / "agents.yaml"
    store_path = tmp_path / "registry.db"

    _write_manifest(
        manifest,
        {
            "schema_version": 1,
            "agents": [
                _descriptor(
                    "agent-a",
                    transport="http",
                    address="http://127.0.0.1:9001",
                    priority=50,
                    quality="high",
                ),
                _descriptor(
                    "agent-b",
                    transport="inproc",
                    address="entrypoint:agent.b:handle",
                    priority=0,
                    quality="standard",
                ),
            ],
        },
    )

    store = SQLiteRegistryStore(store_path, wal=False)
    registry = AgentRegistry(manifest_path=manifest, store=store)
    try:
        registry.load()

        registry.set_status("agent-a", "healthy")
        registry.set_status("agent-b", "degraded")

        route = registry.resolve_method("validate.factcheck")
        assert route is not None
        assert route.agent_id == "agent-a"

        registry.set_status("agent-b", "healthy")
        route2 = registry.resolve_method("validate.factcheck")
        assert route2 is not None
        assert route2.agent_id == "agent-b"

        constrained = registry.resolve_method(
            "validate.factcheck",
            constraints=ResolveConstraints(
                prefer_transport="http", min_quality_tier="high"
            ),
        )
        assert constrained is not None
        assert constrained.agent_id == "agent-a"
        assert constrained.endpoint.transport == "http"
    finally:
        registry.close()


def test_status_defaults_and_heartbeat(tmp_path: Path) -> None:
    manifest = tmp_path / "agents.yaml"
    store_path = tmp_path / "registry.db"

    _write_manifest(
        manifest,
        {
            "schema_version": 1,
            "agents": [
                _descriptor(
                    "agent-z",
                    transport="inproc",
                    address="entrypoint:agent.z:handle",
                    priority=0,
                )
            ],
        },
    )

    store = SQLiteRegistryStore(store_path, wal=False)
    registry = AgentRegistry(manifest_path=manifest, store=store)
    try:
        registry.load()

        initial = registry.get_status("agent-z")
        assert initial.state == "unknown"
        assert initial.last_heartbeat_at is None

        registry.heartbeat("agent-z", {"state": "healthy"})
        updated = registry.get_status("agent-z")

        assert updated.state == "healthy"
        assert updated.last_heartbeat_at is not None
    finally:
        registry.close()
