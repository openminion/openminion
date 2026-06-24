"""Service facade for agent-authored tool lifecycle operations."""

import json
import time
import uuid
from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from openminion.base.time import utc_now_iso
from openminion.modules.tool.registry import ToolRegistry, ToolSpec

from .config import (
    TOOL_AUTHORING_MAX_FAILURE_RATE,
    TOOL_AUTHORING_MIN_SUCCESS_COUNT,
    TOOL_AUTHORING_REQUIRED_TEST_PASS_RATE,
)
from .constants import (
    TOOL_AUTHORING_EVENT_DRAFTED,
    TOOL_AUTHORING_EVENT_FORCE_PROMOTED,
    TOOL_AUTHORING_EVENT_INSPECTED,
    TOOL_AUTHORING_EVENT_INVOKED,
    TOOL_AUTHORING_EVENT_POLICY_GRANT_ISSUED,
    TOOL_AUTHORING_EVENT_POLICY_GRANT_REVOKED,
    TOOL_AUTHORING_EVENT_PROMOTED,
    TOOL_AUTHORING_EVENT_REGISTERED,
    TOOL_AUTHORING_EVENT_REMOVED,
    TOOL_AUTHORING_EVENT_SCOPE_CHANGED,
    TOOL_AUTHORING_SCOPE_POWER_USER,
    TOOL_AUTHORING_STATUS_DRAFTED,
    TOOL_AUTHORING_STATUS_INSPECTED,
    TOOL_AUTHORING_TARGET_DRAFT,
    TOOL_AUTHORING_TARGET_TOOL,
    TOOL_AUTHORING_TIER_EXPERIMENTAL,
    TOOL_AUTHORING_TIER_TRUSTED,
)
from .interfaces import ToolAuthoringServiceInterface
from .runtime.dispatcher import AuthoredToolDispatcher
from .runtime.static import inspect_source, rollup_risk_level
from .runtime.tests import run_tool_tests
from .runtime.grants import issue_power_user_grant, revoke_grant
from .runtime.structural_lint import StructuralLintError, structural_lint
from .runtime.versions import build_tool_name, compute_version_hash
from .schemas import (
    AuthoredToolAuditEventRow,
    AuthoredToolRow,
    ToolAuthorArgs,
    ToolDraftRow,
    ToolInspectArgs,
    ToolRegisterArgs,
)
from .storage import (
    AuthoredToolStore,
    SQLiteToolAuthoringAuditSink,
    encode_audit_details,
)


class ToolAuthoringService(ToolAuthoringServiceInterface):
    """Runtime-owned service for authored-tool lifecycle management."""

    def __init__(
        self,
        *,
        store: AuthoredToolStore,
        audit_sink: SQLiteToolAuthoringAuditSink | None = None,
        sandbox_runner: Any | None = None,
        tool_registry: ToolRegistry | None = None,
        policy_ctl: Any | None = None,
        allowed_dependencies: set[str] | None = None,
    ) -> None:
        self._store = store
        self._audit_sink = audit_sink
        self._sandbox_runner = sandbox_runner
        self._tool_registry = tool_registry
        self._policy_ctl = policy_ctl
        self._allowed_dependencies = set(allowed_dependencies or set())
        self._dispatcher = AuthoredToolDispatcher(
            store=store,
            sandbox_runner=sandbox_runner,
        )

    def close(self) -> None:
        self._store.close()
        if self._audit_sink is not None:
            self._audit_sink.close()

    def get_draft(self, draft_id: str) -> ToolDraftRow | None:
        return self._store.get_draft(draft_id)

    def get_authored_tool(self, tool_name: str) -> AuthoredToolRow | None:
        return self._store.get_authored_tool(tool_name)

    def author_draft(
        self,
        args: dict[str, Any],
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        parsed = ToolAuthorArgs.model_validate(args)
        try:
            lint = structural_lint(
                local_name=parsed.name,
                source_code=parsed.source_code,
                unit_tests_source=parsed.unit_tests_source,
                args_schema=parsed.args_schema,
                dependencies=list(parsed.dependencies),
                allowed_dependencies=self._allowed_dependencies,
            )
        except StructuralLintError as exc:
            return _error(exc.code, exc.message)

        now = utc_now_iso()
        row = ToolDraftRow(
            draft_id=f"draft_{uuid.uuid4().hex}",
            local_name=parsed.name,
            description=parsed.description,
            source_code=parsed.source_code,
            unit_tests_source=parsed.unit_tests_source,
            args_schema_json=_json(parsed.args_schema),
            returns_schema_json=_json(parsed.returns_schema),
            requirements_json=_json(list(parsed.requirements)),
            dependencies_json=_json(list(parsed.dependencies)),
            proposed_scope_tier=parsed.proposed_scope_tier,
            status=TOOL_AUTHORING_STATUS_DRAFTED,
            inspect_result_json=None,
            created_at=now,
            created_by_agent_id=agent_id,
            created_by_session_id=session_id,
        )
        self._store.insert_draft(row)
        self._emit_audit(
            event_type=TOOL_AUTHORING_EVENT_DRAFTED,
            target_kind=TOOL_AUTHORING_TARGET_DRAFT,
            target_id=row.draft_id,
            agent_id=agent_id,
            session_id=session_id,
            details={
                "local_name": row.local_name,
                "stdlib_imports": list(lint.stdlib_imports),
                "external_imports": list(lint.external_imports),
                "test_count": lint.test_count,
            },
        )
        return {
            "ok": True,
            "draft_id": row.draft_id,
            "local_name": row.local_name,
            "status": row.status,
            "stdlib_imports": list(lint.stdlib_imports),
            "external_imports": list(lint.external_imports),
            "test_count": lint.test_count,
        }

    def inspect_draft(
        self,
        args: dict[str, Any],
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        parsed = ToolInspectArgs.model_validate(args)
        draft_row = self._store.get_draft(parsed.draft_id) if parsed.draft_id else None
        source_code = parsed.source_code or (draft_row.source_code if draft_row else "")
        unit_tests_source = parsed.unit_tests_source or (
            draft_row.unit_tests_source if draft_row else ""
        )
        local_name = draft_row.local_name if draft_row else "adhoc_tool"
        static_risk_level, static_findings = inspect_source(
            source_code,
            target_scope_tier=parsed.target_scope_tier,
            allowed_deps=self._allowed_dependencies,
        )
        risk_level = rollup_risk_level(static_findings)
        if static_risk_level and static_risk_level != risk_level:
            risk_level = static_risk_level
        test_results = {"ran": 0, "passed": 0, "failed": 0, "errors": []}
        if parsed.run_tests and self._sandbox_runner is not None:
            run = run_tool_tests(
                source_code=source_code,
                unit_tests_source=unit_tests_source,
                entry_function=local_name,
                sandbox_runner=self._sandbox_runner,
            )
            test_results = {
                "ran": run.ran,
                "passed": run.passed,
                "failed": run.failed,
                "errors": list(run.errors),
            }
            if run.timed_out:
                risk_level = "high"
        elif parsed.run_tests and self._sandbox_runner is None:
            test_results = {
                "ran": 0,
                "passed": 0,
                "failed": 0,
                "errors": [{"test": "pytest", "message": "sandbox_runner_unavailable"}],
            }
            risk_level = "high"

        recommend_register = _recommend_register(
            risk_level=risk_level,
            test_results=test_results,
        )
        payload = {
            "ok": True,
            "draft_id": parsed.draft_id,
            "risk_level": risk_level,
            "findings": [asdict(item) for item in static_findings],
            "test_results": test_results,
            "recommend_register": recommend_register,
            "recommend_reason": _recommend_reason(
                risk_level=risk_level,
                test_results=test_results,
            ),
        }
        if draft_row is not None:
            self._store.update_draft_inspection(
                draft_row.draft_id,
                status=TOOL_AUTHORING_STATUS_INSPECTED,
                inspect_result_json=_json(payload),
            )
            self._emit_audit(
                event_type=TOOL_AUTHORING_EVENT_INSPECTED,
                target_kind=TOOL_AUTHORING_TARGET_DRAFT,
                target_id=draft_row.draft_id,
                agent_id=agent_id,
                session_id=session_id,
                details={
                    "risk_level": risk_level,
                    "findings_count": len(static_findings),
                    "tests_passed": test_results["passed"],
                    "tests_failed": test_results["failed"],
                },
            )
        return payload

    def register_draft(
        self,
        args: dict[str, Any],
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        parsed = ToolRegisterArgs.model_validate(args)
        draft = self._store.get_draft(parsed.draft_id)
        if draft is None:
            return _error("DRAFT_NOT_FOUND", parsed.draft_id)
        version_hash = compute_version_hash(
            source_code=draft.source_code,
            unit_tests_source=draft.unit_tests_source,
        )
        existing = self._store.get_authored_tool_by_name_hash(
            draft.local_name, version_hash
        )
        if existing is not None:
            return {
                "ok": True,
                "tool_name": existing.tool_name,
                "local_name": existing.local_name,
                "version_number": existing.version_number,
                "version_hash": existing.version_hash,
                "tier": existing.tier,
                "min_scope": existing.min_scope,
                "policy_grant_id": existing.policy_grant_id,
                "registered_at": existing.created_at,
                "idempotent": True,
            }
        if (
            draft.status != TOOL_AUTHORING_STATUS_INSPECTED
            or not draft.inspect_result_json
        ):
            return _error(
                "INSPECT_NOT_PASSED", "draft must be inspected before register"
            )
        inspect_result = _parse_json_object(draft.inspect_result_json)
        risk_level = str(inspect_result.get("risk_level", "") or "").strip().lower()
        if risk_level == "critical":
            return _error(
                "INSPECT_NOT_PASSED", "critical findings cannot be registered"
            )
        if risk_level == "high" and not parsed.force:
            return _error("INSPECT_NOT_PASSED", "high risk drafts require force=True")
        if (
            not bool(inspect_result.get("recommend_register", False))
            and not parsed.force
        ):
            return _error(
                "INSPECT_NOT_PASSED", "draft not recommended for registration"
            )

        version_number = self._store.next_version_number(draft.local_name)
        tool_name = build_tool_name(
            local_name=draft.local_name,
            version_number=version_number,
        )
        now = utc_now_iso()
        policy_grant_id: str | None = None
        if self._policy_ctl is None:
            return _error("POLICY_GRANT_FAILED", "policy service unavailable")
        try:
            policy_grant_id = issue_power_user_grant(
                policy_ctl=self._policy_ctl,
                tool_name=tool_name,
                subject_id=str(agent_id or "local"),
            )
        except Exception as exc:
            return _error("POLICY_GRANT_FAILED", str(exc))

        row = AuthoredToolRow(
            tool_name=tool_name,
            local_name=draft.local_name,
            version_number=version_number,
            version_hash=version_hash,
            source_code=draft.source_code,
            unit_tests_source=draft.unit_tests_source,
            args_schema_json=draft.args_schema_json,
            returns_schema_json=draft.returns_schema_json,
            description=draft.description,
            dependencies_json=draft.dependencies_json,
            tier=TOOL_AUTHORING_TIER_EXPERIMENTAL,
            min_scope=TOOL_AUTHORING_SCOPE_POWER_USER,
            policy_grant_id=policy_grant_id,
            created_at=now,
            updated_at=now,
            created_by_agent_id=agent_id,
            promoted_at=None,
            promoted_by=None,
            success_count=0,
            failure_count=0,
            last_invocation_at=None,
            removed_at=None,
            removed_by=None,
        )
        self._store.insert_authored_tool(row)
        self._store.mark_draft_registered(draft.draft_id)
        self._register_runtime_tool(row=row)
        self._emit_audit(
            event_type=TOOL_AUTHORING_EVENT_REGISTERED,
            target_kind=TOOL_AUTHORING_TARGET_TOOL,
            target_id=row.tool_name,
            agent_id=agent_id,
            session_id=session_id,
            version_hash=row.version_hash,
            details={
                "tool_name": row.tool_name,
                "local_name": row.local_name,
                "version_number": row.version_number,
                "version_hash": row.version_hash,
                "min_scope": row.min_scope,
            },
        )
        self._emit_audit(
            event_type=TOOL_AUTHORING_EVENT_POLICY_GRANT_ISSUED,
            target_kind=TOOL_AUTHORING_TARGET_TOOL,
            target_id=row.tool_name,
            agent_id=agent_id,
            session_id=session_id,
            version_hash=row.version_hash,
            details={
                "tool_name": row.tool_name,
                "grant_id": policy_grant_id,
                "scope": TOOL_AUTHORING_SCOPE_POWER_USER,
                "issued_by": "auto_register",
            },
        )
        return {
            "ok": True,
            "tool_name": row.tool_name,
            "local_name": row.local_name,
            "version_number": row.version_number,
            "version_hash": row.version_hash,
            "tier": row.tier,
            "min_scope": row.min_scope,
            "policy_grant_id": row.policy_grant_id,
            "registered_at": row.created_at,
            "idempotent": False,
        }

    def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        result = self._dispatcher.invoke(tool_name, arguments)
        finished_at = utc_now_iso()
        ok = bool(result.get("ok", False))
        row = self._store.get_authored_tool(tool_name)
        version_hash = row.version_hash if row is not None else None
        if row is not None:
            self._store.update_authored_invocation(
                tool_name,
                ok=ok,
                invoked_at=finished_at,
            )
        self._emit_audit(
            event_type=TOOL_AUTHORING_EVENT_INVOKED,
            target_kind=TOOL_AUTHORING_TARGET_TOOL,
            target_id=tool_name,
            agent_id=agent_id,
            session_id=session_id,
            version_hash=version_hash,
            details={
                "tool_name": tool_name,
                "outcome": "ok" if ok else "error",
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return result

    def list_authored_tools(
        self,
        *,
        tier: str = "all",
        include_removed: bool = False,
    ) -> list[dict[str, Any]]:
        rows = self._store.list_authored_tools(
            tier=tier if tier != "all" else None,
            include_removed=include_removed,
        )
        return [_library_row(row) for row in rows]

    def get_authored_tool_detail(self, tool_name: str) -> dict[str, Any] | None:
        row = self._store.get_authored_tool(tool_name)
        if row is None:
            return None
        return {
            **_library_row(row),
            "source_code": row.source_code,
            "unit_tests_source": row.unit_tests_source,
            "args_schema": _parse_json_object(row.args_schema_json),
            "returns_schema": _parse_json_object(row.returns_schema_json),
            "dependencies": _parse_json_array(row.dependencies_json),
            "audit_events": [
                asdict(item)
                for item in self._store.list_audit_events(target_id=tool_name)
            ],
        }

    def promote_tool(
        self,
        tool_name: str,
        *,
        force: bool = False,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        row = self._store.get_authored_tool(tool_name)
        if row is None:
            return _error("TOOL_NOT_FOUND", tool_name)
        inspect_result = self.inspect_draft(
            {
                "source_code": row.source_code,
                "unit_tests_source": row.unit_tests_source,
                "target_scope_tier": row.min_scope,
                "run_tests": True,
            },
            agent_id=actor_id,
            session_id=None,
        )
        risk_level = str(inspect_result.get("risk_level", "") or "").strip().lower()
        if risk_level not in {"low", "medium"}:
            return _error("PROMOTION_REJECTED", f"re-inspect risk={risk_level}")
        total = max(0, int(row.success_count) + int(row.failure_count))
        failure_rate = (int(row.failure_count) / total) if total else 1.0
        if (
            row.success_count < TOOL_AUTHORING_MIN_SUCCESS_COUNT
            or failure_rate > TOOL_AUTHORING_MAX_FAILURE_RATE
        ) and not force:
            return _error("PROMOTION_REJECTED", "telemetry threshold not met")
        now = utc_now_iso()
        self._store.update_authored_promotion(
            tool_name,
            tier=TOOL_AUTHORING_TIER_TRUSTED,
            promoted_at=now,
            promoted_by=actor_id,
        )
        self._emit_audit(
            event_type=TOOL_AUTHORING_EVENT_FORCE_PROMOTED
            if force
            else TOOL_AUTHORING_EVENT_PROMOTED,
            target_kind=TOOL_AUTHORING_TARGET_TOOL,
            target_id=tool_name,
            agent_id=actor_id,
            session_id=None,
            version_hash=row.version_hash,
            details={
                "tool_name": tool_name,
                "promoted_by": actor_id,
                "re_inspect_risk_level": risk_level,
                "success_count": row.success_count,
                "failure_count": row.failure_count,
                "waived_criteria": ["telemetry_threshold"] if force else [],
            },
        )
        return {"ok": True, "tool_name": tool_name, "tier": TOOL_AUTHORING_TIER_TRUSTED}

    def set_tool_scope(
        self,
        tool_name: str,
        *,
        scope: str,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        row = self._store.get_authored_tool(tool_name)
        if row is None:
            return _error("TOOL_NOT_FOUND", tool_name)
        inspect_result = self.inspect_draft(
            {
                "source_code": row.source_code,
                "unit_tests_source": row.unit_tests_source,
                "target_scope_tier": scope,
                "run_tests": True,
            },
            agent_id=actor_id,
            session_id=None,
        )
        risk_level = str(inspect_result.get("risk_level", "") or "").strip().lower()
        if risk_level in {"critical", "high"}:
            return _error("SCOPE_CHANGE_REJECTED", f"re-inspect risk={risk_level}")
        now = utc_now_iso()
        self._store.update_authored_scope(tool_name, scope=scope, updated_at=now)
        self._emit_audit(
            event_type=TOOL_AUTHORING_EVENT_SCOPE_CHANGED,
            target_kind=TOOL_AUTHORING_TARGET_TOOL,
            target_id=tool_name,
            agent_id=actor_id,
            session_id=None,
            version_hash=row.version_hash,
            details={
                "tool_name": tool_name,
                "from_scope": row.min_scope,
                "to_scope": scope,
                "changed_by": actor_id,
            },
        )
        return {"ok": True, "tool_name": tool_name, "min_scope": scope}

    def remove_tool(
        self,
        tool_name: str,
        *,
        actor_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        row = self._store.get_authored_tool(tool_name)
        if row is None:
            return _error("TOOL_NOT_FOUND", tool_name)
        now = utc_now_iso()
        self._store.mark_tool_removed(tool_name, removed_at=now, removed_by=actor_id)
        if self._policy_ctl is not None and row.policy_grant_id:
            revoked = revoke_grant(
                policy_ctl=self._policy_ctl, grant_id=row.policy_grant_id
            )
            if revoked:
                self._store.attach_policy_grant(
                    tool_name, grant_id=None, updated_at=now
                )
                self._emit_audit(
                    event_type=TOOL_AUTHORING_EVENT_POLICY_GRANT_REVOKED,
                    target_kind=TOOL_AUTHORING_TARGET_TOOL,
                    target_id=tool_name,
                    agent_id=actor_id,
                    session_id=None,
                    version_hash=row.version_hash,
                    details={
                        "tool_name": tool_name,
                        "grant_id": row.policy_grant_id,
                    },
                )
        self._emit_audit(
            event_type=TOOL_AUTHORING_EVENT_REMOVED,
            target_kind=TOOL_AUTHORING_TARGET_TOOL,
            target_id=tool_name,
            agent_id=actor_id,
            session_id=None,
            version_hash=row.version_hash,
            details={
                "tool_name": tool_name,
                "reason": reason or "",
                "removed_by": actor_id,
            },
        )
        return {"ok": True, "tool_name": tool_name, "removed": True}

    def register_runtime_tools(self, registry: Any) -> list[str]:
        tool_names: list[str] = []
        self._tool_registry = registry
        for row in self._store.list_registered():
            self._register_runtime_tool(row=row)
            tool_names.append(row.tool_name)
        return tool_names

    def _register_runtime_tool(self, *, row: AuthoredToolRow) -> None:
        if self._tool_registry is None:
            return
        if row.tool_name in self._tool_registry.list():
            return

        args_model = build_args_model(
            tool_name=row.tool_name,
            args_schema=_parse_json_object(row.args_schema_json),
        )

        def _handler(arguments: dict[str, Any], ctx: Any) -> dict[str, Any]:
            service = getattr(ctx, "authored_tools_api", None) or self
            return service.invoke(
                row.tool_name,
                arguments,
                agent_id=getattr(ctx, "agent_id", None),
                session_id=getattr(ctx, "session_id", None),
            )

        spec = ToolSpec(
            name=row.tool_name,
            args_model=args_model,
            min_scope=str(row.min_scope or TOOL_AUTHORING_SCOPE_POWER_USER),
            handler=_handler,
            dangerous=False,
            idempotent=False,
            tags=(
                "authored",
                "origin:authored",
                str(row.tier),
                f"v{row.version_number}",
            ),
            capabilities=("authored", str(row.tier)),
            prompt_visible_runtime_name=True,
        )
        spec.description = str(row.description or "").strip()
        self._tool_registry.register(spec)
        if self._policy_ctl is not None and callable(
            getattr(self._policy_ctl, "register_risk", None)
        ):
            self._policy_ctl.register_risk(
                row.tool_name,
                {
                    "risk_class": "exec",
                    "side_effects": "local",
                    "reversibility": "unknown",
                    "default_confirm": True,
                    "sensitive_targets": [],
                },
            )

    def _emit_audit(
        self,
        *,
        event_type: str,
        target_kind: str,
        target_id: str,
        details: dict[str, Any],
        agent_id: str | None = None,
        session_id: str | None = None,
        version_hash: str | None = None,
    ) -> None:
        row = AuthoredToolAuditEventRow(
            event_id=uuid.uuid4().hex,
            timestamp=utc_now_iso(),
            event_type=event_type,
            target_kind=target_kind,
            target_id=target_id,
            agent_id=agent_id,
            session_id=session_id,
            version_hash=version_hash,
            details_json=encode_audit_details(details),
        )
        self._store.insert_audit_event(row)
        if self._audit_sink is not None:
            self._audit_sink.append_event(row)


def build_args_model(*, tool_name: str, args_schema: dict[str, Any]) -> type[BaseModel]:
    properties = (
        args_schema.get("properties", {}) if isinstance(args_schema, dict) else {}
    )
    required = {
        str(item).strip()
        for item in (
            args_schema.get("required", []) if isinstance(args_schema, dict) else []
        )
        if str(item).strip()
    }
    fields: dict[str, tuple[Any, Any]] = {}
    for key, raw in properties.items() if isinstance(properties, dict) else []:
        token = str(key).strip()
        if not token:
            continue
        schema = raw if isinstance(raw, dict) else {}
        py_type = _schema_type_to_python(schema)
        default = ... if token in required else None
        fields[token] = (
            py_type,
            Field(
                default,
                description=str(schema.get("description", "") or "").strip() or None,
            ),
        )
    model_name = (
        "".join(
            part.title()
            for part in tool_name.replace(".", "_").replace("@", "_").split("_")
        )
        or "AuthoredToolArgs"
    )
    return create_model(
        model_name,
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def _schema_type_to_python(schema: dict[str, Any]) -> Any:
    raw_type = str(schema.get("type", "string") or "string").strip().lower()
    if raw_type == "integer":
        return int
    if raw_type == "number":
        return float
    if raw_type == "boolean":
        return bool
    if raw_type == "array":
        return list[Any]
    if raw_type == "object":
        return dict[str, Any]
    return str


def _recommend_register(*, risk_level: str, test_results: dict[str, Any]) -> bool:
    if risk_level in {"critical", "high"}:
        return False
    ran = int(test_results.get("ran", 0) or 0)
    passed = int(test_results.get("passed", 0) or 0)
    failed = int(test_results.get("failed", 0) or 0)
    errors = list(test_results.get("errors", []) or [])
    if failed > 0 or errors:
        return False
    if ran <= 0:
        return False
    return (passed / ran) >= TOOL_AUTHORING_REQUIRED_TEST_PASS_RATE


def _recommend_reason(*, risk_level: str, test_results: dict[str, Any]) -> str:
    if risk_level == "critical":
        return "critical static findings detected"
    if risk_level == "high":
        return "high static risk detected"
    failed = int(test_results.get("failed", 0) or 0)
    if failed:
        return f"{failed} held-out tests failed"
    if list(test_results.get("errors", []) or []):
        return "test execution produced errors"
    if int(test_results.get("ran", 0) or 0) <= 0:
        return "no held-out tests ran"
    return "all checks passed"


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _parse_json_object(raw: str) -> dict[str, Any]:
    payload = json.loads(str(raw or "{}"))
    return dict(payload) if isinstance(payload, dict) else {}


def _parse_json_array(raw: str) -> list[Any]:
    payload = json.loads(str(raw or "[]"))
    return list(payload) if isinstance(payload, list) else []


def _library_row(row: AuthoredToolRow) -> dict[str, Any]:
    return {
        "tool_name": row.tool_name,
        "local_name": row.local_name,
        "version_number": row.version_number,
        "tier": row.tier,
        "min_scope": row.min_scope,
        "description": row.description,
        "success_count": row.success_count,
        "failure_count": row.failure_count,
        "last_invocation_at": row.last_invocation_at,
        "removed_at": row.removed_at,
    }


__all__ = ["ToolAuthoringService", "build_args_model"]
