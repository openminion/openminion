import datetime
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Protocol

from openminion.base.runtime.constants import (
    RUNTIME_POLICY_OUTCOME_ALLOW_WITH_CONSTRAINTS,
    RUNTIME_POLICY_OUTCOME_CONFIRM,
    RUNTIME_POLICY_OUTCOME_DENY,
    RUNTIME_TOOL_OUTCOME_BLOCKED,
    RUNTIME_TOOL_OUTCOME_CACHED,
    RUNTIME_TOOL_OUTCOME_COMPLETED,
    RUNTIME_TOOL_OUTCOME_ERROR,
)
from openminion.base.runtime.interfaces import (
    RUNTIME_INTERFACE_VERSION,
    ensure_runtime_component_compatibility,
)
from openminion.base.runtime.sandbox import (
    ExecutionSandboxSpec,
    ExecSpec,
    FsWriteSpec,
    FsDeleteSpec,
    NetFetchSpec,
    SandboxRunner,
)


@dataclass
class ToolCall:
    """A single side-effecting tool call request."""

    tool_call_id: str
    name: str
    kind: str  # "exec" | "fs.write" | "fs.delete" | "net.fetch"
    spec: ExecSpec | FsWriteSpec | FsDeleteSpec | NetFetchSpec
    idempotency_key: str | None = None


@dataclass
class RuntimeContext:
    """Execution context propagated from the agent turn into side-effect calls."""

    trace_id: str
    agent_id: str
    session_id: str
    run_id: str
    workspace_root: str
    tool_caps: dict = field(default_factory=dict)
    task_id: str | None = None
    plan_id: str | None = None
    step_id: str | None = None
    attempt: int = 1
    turn_id: str | None = None
    pack_id: str | None = None


@dataclass
class PolicyDecision:
    """Decision returned by a policy evaluation call."""

    outcome: str
    policy_request_id: str
    constraints: dict = field(default_factory=dict)


@dataclass
class ToolExecutionResult:
    tool_call_id: str
    outcome: str
    result: Any = None  # ExecResult | FsResult | NetResult
    error: str | None = None
    from_cache: bool = False


class PolicyClient(Protocol):
    """Caller-provided policy evaluation interface."""

    contract_version: str

    def evaluate(self, tool_call: ToolCall, ctx: RuntimeContext) -> PolicyDecision: ...


ConfirmHandler = Callable[[ToolCall, RuntimeContext, PolicyDecision], bool]

_EventHook = Callable[[str, dict], None]


class RuntimeEngine:
    """Orchestrates side-effect execution through the policy handshake."""

    contract_version = RUNTIME_INTERFACE_VERSION

    def __init__(
        self,
        runner: SandboxRunner,
        policy: PolicyClient,
        on_event: _EventHook | None = None,
        on_confirm: ConfirmHandler | None = None,
        task_ctl: Any | None = None,
        blast_radius_adapter: Any | None = None,
    ) -> None:
        ensure_runtime_component_compatibility(runner, component_type="runner")
        ensure_runtime_component_compatibility(policy, component_type="policy")
        self._runner = runner
        self._policy = policy
        self._on_event = on_event
        self._on_confirm = on_confirm
        self._task_ctl = task_ctl
        self._blast_radius_adapter = blast_radius_adapter
        self._idempotency_cache: dict[str, ToolExecutionResult] = {}

    def execute_tool_call(
        self, tool_call: ToolCall, ctx: RuntimeContext
    ) -> ToolExecutionResult:
        """Execute a single side-effecting tool call through the full policy handshake."""
        correlation = self._build_correlation(tool_call=tool_call, ctx=ctx)

        cached = self._replay_cached_result(tool_call=tool_call)
        if cached is not None:
            return cached

        self._emit(
            "tool.call.requested",
            {**correlation, "name": tool_call.name, "kind": tool_call.kind},
        )

        policy_req_id = str(uuid.uuid4())
        self._emit(
            "policy.requested", {**correlation, "policy_request_id": policy_req_id}
        )
        decision = self._policy.evaluate(tool_call, ctx)

        if self._blast_radius_adapter is not None:
            kind = str(getattr(tool_call, "kind", "") or "").strip().lower()
            if kind == "exec":
                spec_shape = SimpleNamespace(
                    name=str(getattr(tool_call, "name", "") or "").strip() or "exec",
                    min_scope="POWER_USER",
                    dangerous=True,
                )
            elif kind in ("fs.write", "fs.delete"):
                spec_shape = SimpleNamespace(
                    name=str(getattr(tool_call, "name", "") or "").strip() or kind,
                    min_scope="WRITE_SAFE",
                    dangerous=True,
                )
            elif kind == "net.fetch":
                spec_shape = SimpleNamespace(
                    name=str(getattr(tool_call, "name", "") or "").strip()
                    or "net.fetch",
                    min_scope="WRITE_SAFE",
                    dangerous=False,
                    blast_radius="remote_mutation",
                )
            else:
                spec_shape = SimpleNamespace(
                    name=str(getattr(tool_call, "name", "") or "").strip()
                    or kind
                    or "runtime_engine_tool",
                    min_scope="READ_ONLY",
                    dangerous=False,
                )
            self._blast_radius_adapter.step(spec_shape)

        self._emit(
            "policy.decision.created",
            {
                **correlation,
                "policy_request_id": decision.policy_request_id,
                "outcome": decision.outcome,
            },
        )

        if decision.outcome == RUNTIME_POLICY_OUTCOME_DENY:
            return self._blocked_result(
                tool_call=tool_call,
                correlation=correlation,
                policy_request_id=decision.policy_request_id,
                reason="policy_deny",
            )

        if decision.outcome == RUNTIME_POLICY_OUTCOME_CONFIRM:
            approved = self._on_confirm is not None and self._on_confirm(
                tool_call, ctx, decision
            )
            if not approved:
                if self._on_confirm is None:
                    self._record_pending_approval(ctx=ctx, decision=decision)
                return self._blocked_result(
                    tool_call=tool_call,
                    correlation=correlation,
                    policy_request_id=decision.policy_request_id,
                    reason="confirm_denied",
                )

        sandbox = ExecutionSandboxSpec.build(
            workspace_root=ctx.workspace_root,
            tool_caps=ctx.tool_caps,
            policy_constraints=decision.constraints
            if decision.outcome
            in (
                RUNTIME_POLICY_OUTCOME_ALLOW_WITH_CONSTRAINTS,
                RUNTIME_POLICY_OUTCOME_CONFIRM,
            )
            else {},
        )
        sandbox.idempotency_key = tool_call.idempotency_key

        exec_payload = {**correlation, "policy_request_id": decision.policy_request_id}
        result = self._dispatch(tool_call, sandbox, exec_payload)

        final_event = (
            "tool.call.blocked"
            if result.outcome
            in (RUNTIME_TOOL_OUTCOME_BLOCKED, RUNTIME_TOOL_OUTCOME_ERROR)
            else "tool.call.completed"
        )
        self._emit(final_event, {**correlation, "outcome": result.outcome})

        if (
            tool_call.idempotency_key
            and result.outcome == RUNTIME_TOOL_OUTCOME_COMPLETED
        ):
            self._idempotency_cache[tool_call.idempotency_key] = result

        return result

    def _emit(self, event: str, payload: dict) -> None:
        if self._on_event:
            ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._on_event(event, {"ts": ts, **payload})

    def _build_correlation(
        self,
        *,
        tool_call: ToolCall,
        ctx: RuntimeContext,
    ) -> dict[str, str]:
        return {
            "tool_call_id": tool_call.tool_call_id,
            "trace_id": ctx.trace_id,
            "agent_id": ctx.agent_id,
            "session_id": ctx.session_id,
            "run_id": ctx.run_id,
        }

    def _replay_cached_result(
        self,
        *,
        tool_call: ToolCall,
    ) -> ToolExecutionResult | None:
        if (
            not tool_call.idempotency_key
            or tool_call.idempotency_key not in self._idempotency_cache
        ):
            return None
        cached = self._idempotency_cache[tool_call.idempotency_key]
        return ToolExecutionResult(
            tool_call_id=tool_call.tool_call_id,
            outcome=RUNTIME_TOOL_OUTCOME_CACHED,
            result=cached.result,
            from_cache=True,
        )

    def _blocked_result(
        self,
        *,
        tool_call: ToolCall,
        correlation: dict[str, str],
        policy_request_id: str,
        reason: str,
    ) -> ToolExecutionResult:
        self._emit(
            "runtime.violation",
            {
                **correlation,
                "policy_request_id": policy_request_id,
                "reason": reason,
            },
        )
        self._emit("tool.call.blocked", {**correlation, "reason": reason})
        return ToolExecutionResult(
            tool_call_id=tool_call.tool_call_id,
            outcome=RUNTIME_TOOL_OUTCOME_BLOCKED,
            error=reason,
        )

    def _record_pending_approval(
        self,
        *,
        ctx: RuntimeContext,
        decision: PolicyDecision,
    ) -> None:
        if self._task_ctl is None:
            return
        record_pending = getattr(self._task_ctl, "record_pending_action", None)
        if not callable(record_pending):
            return
        task_id = str(ctx.task_id or "").strip()
        plan_id = str(ctx.plan_id or "").strip()
        step_id = str(ctx.step_id or "").strip()
        if not task_id or not plan_id or not step_id:
            return

        from openminion.modules.task.schemas import ResumePointer

        cursor = ResumePointer(
            task_id=task_id,
            plan_id=plan_id,
            step_id=step_id,
            attempt=max(1, int(ctx.attempt or 1)),
            trace_id=ctx.trace_id,
            turn_id=ctx.turn_id,
            pack_id=ctx.pack_id,
        )
        record_pending(
            policy_request_id=decision.policy_request_id,
            cursor=cursor,
            reason="policy_confirm",
        )

    def _dispatch(
        self,
        tool_call: ToolCall,
        sandbox: ExecutionSandboxSpec,
        audit_payload: dict,
    ) -> ToolExecutionResult:
        try:
            if tool_call.kind == "exec":
                assert isinstance(tool_call.spec, ExecSpec)
                self._emit("runtime.exec.started", audit_payload)
                raw = self._runner.run_exec(tool_call.spec, sandbox)
                self._emit(
                    "runtime.exec.completed",
                    {
                        **audit_payload,
                        "returncode": raw.returncode,
                        "timed_out": raw.timed_out,
                    },
                )
                return ToolExecutionResult(
                    tool_call_id=tool_call.tool_call_id,
                    outcome=RUNTIME_TOOL_OUTCOME_COMPLETED,
                    result=raw,
                )

            if tool_call.kind == "fs.write":
                assert isinstance(tool_call.spec, FsWriteSpec)
                raw = self._runner.fs_write(tool_call.spec, sandbox)
                self._emit(
                    "runtime.fs.write",
                    {
                        **audit_payload,
                        "path": tool_call.spec.path,
                        "success": raw.success,
                    },
                )
                return ToolExecutionResult(
                    tool_call_id=tool_call.tool_call_id,
                    outcome=RUNTIME_TOOL_OUTCOME_COMPLETED,
                    result=raw,
                )

            if tool_call.kind == "fs.delete":
                assert isinstance(tool_call.spec, FsDeleteSpec)
                raw = self._runner.fs_delete(tool_call.spec, sandbox)
                self._emit(
                    "runtime.fs.delete",
                    {
                        **audit_payload,
                        "path": tool_call.spec.path,
                        "success": raw.success,
                    },
                )
                return ToolExecutionResult(
                    tool_call_id=tool_call.tool_call_id,
                    outcome=RUNTIME_TOOL_OUTCOME_COMPLETED,
                    result=raw,
                )

            if tool_call.kind == "net.fetch":
                assert isinstance(tool_call.spec, NetFetchSpec)
                self._emit(
                    "runtime.net.requested",
                    {
                        **audit_payload,
                        "url": tool_call.spec.url,
                        "method": tool_call.spec.method,
                    },
                )
                raw = self._runner.net_fetch(tool_call.spec, sandbox)
                return ToolExecutionResult(
                    tool_call_id=tool_call.tool_call_id,
                    outcome=RUNTIME_TOOL_OUTCOME_COMPLETED,
                    result=raw,
                )

            return ToolExecutionResult(
                tool_call_id=tool_call.tool_call_id,
                outcome=RUNTIME_TOOL_OUTCOME_ERROR,
                error=f"unknown_kind:{tool_call.kind!r}",
            )

        except PermissionError as exc:
            self._emit(
                "runtime.violation",
                {**audit_payload, "reason": "enforcement_denied", "detail": str(exc)},
            )
            return ToolExecutionResult(
                tool_call_id=tool_call.tool_call_id,
                outcome=RUNTIME_TOOL_OUTCOME_BLOCKED,
                error=str(exc),
            )
        except Exception as exc:
            return ToolExecutionResult(
                tool_call_id=tool_call.tool_call_id,
                outcome=RUNTIME_TOOL_OUTCOME_ERROR,
                error=str(exc),
            )
