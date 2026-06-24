from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import (
    RuntimeContext,
    build_runtime_repositories,
    resolve_tool_runtime_audit_mode,
)
from openminion.tools.fetch import plugin as fetch_plugin


def _ctx(
    tmp_path: Path,
    *,
    storage_path: Path,
    audit_mode: str | None = None,
) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    audit_cfg: dict[str, object] = {}
    if audit_mode is not None:
        audit_cfg["write_mode"] = audit_mode
    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "context_metadata": {"storage_path": str(storage_path)},
            "audit": audit_cfg,
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
        repositories=build_runtime_repositories(
            context_metadata={"storage_path": str(storage_path)}
        ),
    )


def _ctx_without_repositories(
    tmp_path: Path,
    *,
    storage_path: Path,
    audit_mode: str | None = None,
) -> RuntimeContext:
    workspace = tmp_path / "workspace-unwired"
    run_root = tmp_path / "run-unwired"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    audit_cfg: dict[str, object] = {}
    if audit_mode is not None:
        audit_cfg["write_mode"] = audit_mode
    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "context_metadata": {"storage_path": str(storage_path)},
            "audit": audit_cfg,
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
    )


def _read_storage_event(storage_path: Path, event_id: str) -> dict[str, object]:
    conn = sqlite3.connect(str(storage_path))
    try:
        row = conn.execute(
            """
            SELECT event_json
            FROM tool_runtime_audit_events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return json.loads(str(row[0]))


def _read_jsonl_events(ctx: RuntimeContext) -> list[dict[str, Any]]:
    path = ctx.run_root / "audit.jsonl"
    if not path.exists():
        return []
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [json.loads(line) for line in lines]


def _read_storage_events(storage_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(storage_path))
    try:
        rows = conn.execute(
            """
            SELECT event_json
            FROM tool_runtime_audit_events
            ORDER BY ts ASC
            """
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(str(row[0])) for row in rows]


def test_write_audit_event_dual_writes_with_stable_event_id(tmp_path: Path) -> None:
    storage_path = tmp_path / "audit-storage.db"
    ctx = _ctx(tmp_path, storage_path=storage_path)

    ctx.write_audit_event({"event": "first"})
    line = (
        (ctx.run_root / "audit.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()[0]
    )
    jsonl_event = json.loads(line)
    event_id = str(jsonl_event["event_id"])
    assert event_id
    assert jsonl_event["event"] == "first"

    storage_event = _read_storage_event(storage_path, event_id)
    assert storage_event["event_id"] == event_id
    assert storage_event["event"] == "first"


def test_write_audit_event_preserves_explicit_event_id(tmp_path: Path) -> None:
    storage_path = tmp_path / "audit-storage.db"
    ctx = _ctx(tmp_path, storage_path=storage_path)

    explicit_event_id = "evt-explicit-123"
    ctx.write_audit_event({"event_id": explicit_event_id, "event": "second"})
    line = (
        (ctx.run_root / "audit.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()[0]
    )
    jsonl_event = json.loads(line)
    assert jsonl_event["event_id"] == explicit_event_id

    storage_event = _read_storage_event(storage_path, explicit_event_id)
    assert storage_event["event_id"] == explicit_event_id
    assert storage_event["event"] == "second"


def test_write_mode_jsonl_only_skips_storage_sink(tmp_path: Path) -> None:
    storage_path = tmp_path / "audit-storage.db"
    ctx = _ctx(tmp_path, storage_path=storage_path, audit_mode="jsonl_only")

    ctx.write_audit_event({"event": "jsonl-only"})
    line = (
        (ctx.run_root / "audit.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()[0]
    )
    jsonl_event = json.loads(line)
    assert jsonl_event["event"] == "jsonl-only"
    assert not storage_path.exists()


def test_write_mode_storage_only_skips_jsonl(tmp_path: Path) -> None:
    storage_path = tmp_path / "audit-storage.db"
    ctx = _ctx(tmp_path, storage_path=storage_path, audit_mode="storage_only")

    ctx.write_audit_event({"event": "storage-only"})
    assert not (ctx.run_root / "audit.jsonl").exists()

    conn = sqlite3.connect(str(storage_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM tool_runtime_audit_events WHERE json_extract(event_json, '$.event') = 'storage-only'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert int(row[0]) == 1


def test_write_mode_off_disables_both_sinks(tmp_path: Path) -> None:
    storage_path = tmp_path / "audit-storage.db"
    ctx = _ctx(tmp_path, storage_path=storage_path, audit_mode="off")

    ctx.write_audit_event({"event": "disabled"})
    assert not (ctx.run_root / "audit.jsonl").exists()
    assert not storage_path.exists()


def test_write_audit_event_dual_mode_builds_storage_sink_without_prewired_repositories(
    tmp_path: Path,
) -> None:
    storage_path = tmp_path / "audit-storage.db"
    ctx = _ctx_without_repositories(
        tmp_path, storage_path=storage_path, audit_mode="dual"
    )

    ctx.write_audit_event({"event": "dual-unwired"})

    jsonl_events = _read_jsonl_events(ctx)
    storage_events = _read_storage_events(storage_path)
    assert len(jsonl_events) == 1
    assert len(storage_events) == 1
    assert jsonl_events[0]["event"] == "dual-unwired"
    assert storage_events[0]["event"] == "dual-unwired"
    assert jsonl_events[0]["event_id"] == storage_events[0]["event_id"]


def test_write_audit_event_storage_only_builds_storage_sink_without_prewired_repositories(
    tmp_path: Path,
) -> None:
    storage_path = tmp_path / "audit-storage.db"
    ctx = _ctx_without_repositories(
        tmp_path, storage_path=storage_path, audit_mode="storage_only"
    )

    ctx.write_audit_event({"event": "storage-only-unwired"})

    assert not (ctx.run_root / "audit.jsonl").exists()
    storage_events = _read_storage_events(storage_path)
    assert len(storage_events) == 1
    assert storage_events[0]["event"] == "storage-only-unwired"


def test_resolve_tool_runtime_audit_mode_honors_precedence(monkeypatch) -> None:
    policy = Policy(
        raw={
            "audit": {"write_mode": "storage_only"},
            "context_metadata": {"tool_runtime_audit_mode": "jsonl_only"},
        }
    )
    monkeypatch.setenv("OPENMINION_TOOL_RUNTIME_AUDIT_MODE", "off")

    # explicit context override wins
    assert (
        resolve_tool_runtime_audit_mode(
            policy=policy, context_metadata={"tool_runtime_audit_mode": "dual"}
        )
        == "dual"
    )
    # without explicit context override, env wins over policy defaults
    assert resolve_tool_runtime_audit_mode(policy=policy) == "off"


def test_resolve_tool_runtime_audit_mode_invalid_value_falls_back_to_dual(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENMINION_TOOL_RUNTIME_AUDIT_MODE", raising=False)
    policy = Policy(raw={"audit": {"write_mode": "totally-invalid-mode"}})
    assert resolve_tool_runtime_audit_mode(policy=policy) == "dual"


def test_fetch_success_events_are_observable_in_both_audit_sinks(
    monkeypatch, tmp_path: Path
) -> None:
    class _SuccessProvider:
        name = "core-http"
        capabilities = {}

        def fetch(self, request: dict[str, Any], _ctx: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "final_url": str(request.get("url", "")),
                "status_code": 200,
                "headers": {"content-type": "text/html; charset=utf-8"},
                "content_type": "text/html; charset=utf-8",
                "content_bytes": 12,
                "raw_body": b"<h1>hello</h1>",
                "extracted_text": "hello",
                "title": "hello",
                "warnings": [],
                "backend": "core-http",
            }

    class _Registry:
        def __init__(self) -> None:
            self._provider = _SuccessProvider()

        def list_names(self) -> list[str]:
            return ["core-http"]

        def get(self, name: str) -> Any:
            assert name == "core-http"
            return self._provider

    monkeypatch.setattr(fetch_plugin, "_ensure_provider_registry", lambda: _Registry())

    storage_path = tmp_path / "audit-storage.db"
    ctx = _ctx(tmp_path, storage_path=storage_path, audit_mode="dual")
    payload = fetch_plugin._h_get({"url": "https://example.com"}, ctx)
    assert payload["ok"] is True

    jsonl_events = _read_jsonl_events(ctx)
    storage_events = _read_storage_events(storage_path)
    assert jsonl_events
    assert storage_events
    assert {str(item["event_id"]) for item in jsonl_events} == {
        str(item["event_id"]) for item in storage_events
    }
    assert any(
        str(item.get("event", "")) == "fetch.completed" and bool(item.get("ok", False))
        for item in storage_events
    )


def test_fetch_failure_events_are_observable_in_both_audit_sinks(
    monkeypatch, tmp_path: Path
) -> None:
    class _FailureProvider:
        name = "core-http"
        capabilities = {}

        def fetch(self, request: dict[str, Any], _ctx: Any) -> dict[str, Any]:
            del request
            return {
                "ok": False,
                "error": {
                    "code": "UPSTREAM_ERROR",
                    "message": "upstream failed",
                    "details": {"source": "test"},
                },
                "backend": "core-http",
            }

    class _Registry:
        def __init__(self) -> None:
            self._provider = _FailureProvider()

        def list_names(self) -> list[str]:
            return ["core-http"]

        def get(self, name: str) -> Any:
            assert name == "core-http"
            return self._provider

    monkeypatch.setattr(fetch_plugin, "_ensure_provider_registry", lambda: _Registry())

    storage_path = tmp_path / "audit-storage.db"
    ctx = _ctx(tmp_path, storage_path=storage_path, audit_mode="dual")
    payload = fetch_plugin._h_get({"url": "https://example.com"}, ctx)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "UPSTREAM_ERROR"

    jsonl_events = _read_jsonl_events(ctx)
    storage_events = _read_storage_events(storage_path)
    assert jsonl_events
    assert storage_events
    assert {str(item["event_id"]) for item in jsonl_events} == {
        str(item["event_id"]) for item in storage_events
    }
    assert any(
        str(item.get("event", "")) == "fetch.completed"
        and bool(item.get("ok", True)) is False
        and str(item.get("code", "")) == "UPSTREAM_ERROR"
        for item in storage_events
    )
