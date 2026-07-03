from __future__ import annotations

import json
from pathlib import Path

from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.family.events import (
    emit_family_event,
    emit_provider_attempt,
)


def _make_ctx(tmp_path: Path) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    raw: dict = {
        "workspace_root": str(workspace),
        "paths": {
            "read_allow": [str(workspace)],
            "write_allow": [str(workspace)],
            "deny": [],
        },
        "commands": {"mode": "allowlist", "allow": []},
    }
    return RuntimeContext(
        policy=Policy(raw=raw),
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
    )


def _read_audit(ctx: RuntimeContext) -> list[dict]:
    audit_path = ctx.run_root / "audit.jsonl"
    if not audit_path.exists():
        return []
    return [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_emit_family_event_writes_event_to_audit_log(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    emit_family_event(ctx, event="test.event", payload={"key": "val"})
    records = _read_audit(ctx)
    assert len(records) == 1
    assert records[0]["event"] == "test.event"
    assert records[0]["key"] == "val"


def test_emit_family_event_no_payload_writes_event_only(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    emit_family_event(ctx, event="test.bare")
    records = _read_audit(ctx)
    assert len(records) == 1
    assert records[0]["event"] == "test.bare"


def test_emit_family_event_ignores_non_runtime_context() -> None:
    from types import SimpleNamespace

    ctx = SimpleNamespace(some_field="not_a_runtime_context")
    emit_family_event(ctx, event="test.event", payload={"x": 1})


def test_emit_family_event_ignores_none_context() -> None:
    emit_family_event(None, event="test.event", payload={"x": 1})


def test_emit_family_event_tolerates_write_failure(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    original = ctx.write_audit_event

    def _bad_write(event):  # noqa: ANN001
        raise RuntimeError("simulated audit write failure")

    ctx.write_audit_event = _bad_write
    emit_family_event(ctx, event="test.event", payload={"x": 1})
    ctx.write_audit_event = original


def test_emit_family_event_merges_payload_fields(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    emit_family_event(
        ctx,
        event="search.provider.selected",
        payload={
            "requested_provider": "auto",
            "selected_provider": "brave",
            "attempt_index": 1,
        },
    )
    records = _read_audit(ctx)
    assert records[0]["event"] == "search.provider.selected"
    assert records[0]["requested_provider"] == "auto"
    assert records[0]["selected_provider"] == "brave"
    assert records[0]["attempt_index"] == 1


def test_emit_provider_attempt_adds_attempt_index(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    emit_provider_attempt(
        ctx,
        event="search.provider.selected",
        attempt_index=2,
        payload={"selected_provider": "tavily"},
    )
    records = _read_audit(ctx)
    assert records[0]["attempt_index"] == 2
    assert records[0]["selected_provider"] == "tavily"


def test_emit_provider_attempt_no_payload_uses_attempt_index_only(
    tmp_path: Path,
) -> None:
    ctx = _make_ctx(tmp_path)
    emit_provider_attempt(ctx, event="fetch.provider.selected", attempt_index=1)
    records = _read_audit(ctx)
    assert records[0]["event"] == "fetch.provider.selected"
    assert records[0]["attempt_index"] == 1


def test_emit_provider_attempt_ignores_non_runtime_context() -> None:
    emit_provider_attempt(None, event="test.event", attempt_index=1, payload={"x": 1})
