from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from openminion.modules.tool import ToolRegistry
from openminion.modules.tool.authoring import (
    SQLiteToolAuthoringAuditSink,
    ToolAuthoringService,
    build_authored_tool_store,
)


@dataclass
class FakeExecResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    timed_out: bool = False


class RecordingSandboxRunner:
    def __init__(self, result: FakeExecResult | None = None) -> None:
        self.result = result or FakeExecResult()
        self.calls: list[tuple[Any, Any]] = []

    def run_exec(self, spec, sandbox):
        self.calls.append((spec, sandbox))
        return self.result


class FakePolicyCtl:
    def __init__(self) -> None:
        self.grants: dict[str, dict[str, Any]] = {}
        self.registered_risks: dict[str, Any] = {}

    def create_grant(self, grant) -> str:
        grant_id = f"grant-{uuid4().hex}"
        self.grants[grant_id] = {
            "grant_id": grant_id,
            "tool": getattr(grant, "tool", ""),
            "subject_id": getattr(grant, "subject_id", ""),
            "active": True,
        }
        return grant_id

    def revoke_grant(self, grant_id: str) -> bool:
        grant = self.grants.get(grant_id)
        if grant is None:
            return False
        grant["active"] = False
        return True

    def list_grants(self, active_only: bool = False, **kwargs) -> list[Any]:
        del kwargs
        rows = list(self.grants.values())
        if active_only:
            rows = [row for row in rows if bool(row.get("active", False))]
        return rows

    def register_risk(self, key: str, spec: Any) -> None:
        self.registered_risks[key] = spec


def build_service(
    tmp_path: Path,
    *,
    sandbox_runner: Any | None = None,
    policy_ctl: Any | None = None,
    allowed_dependencies: set[str] | None = None,
    registry: ToolRegistry | None = None,
) -> ToolAuthoringService:
    store_path = tmp_path / "authored_tools.sqlite"
    store = build_authored_tool_store(sqlite_path=store_path)
    audit_sink = SQLiteToolAuthoringAuditSink(tmp_path / "audit.sqlite")
    return ToolAuthoringService(
        store=store,
        audit_sink=audit_sink,
        sandbox_runner=sandbox_runner,
        tool_registry=registry or ToolRegistry(),
        policy_ctl=policy_ctl,
        allowed_dependencies=allowed_dependencies or set(),
    )
