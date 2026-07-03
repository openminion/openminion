import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED
from ..errors import ToolRuntimeError
from ..plugin_contract import (
    ArtifactSink,
    CASArtifactSink,
    EventSink,
    HealthStatus,
    MemoryArtifactSink,
    NullEventSink,
    ToolMethod,
    ToolPlugin,
    ToolDefinition,
    PolicyDecision,
    PolicyHook,
    ToolCapabilities,
    ToolContext,
    ToolDescriptor,
    ToolError,
    ToolInvocation,
    ToolResult,
    ToolSchemaBundle,
)
from ..constants import (
    TOOL_POLICY_ACTION_ALLOW,
    TOOL_POLICY_ACTION_DENY,
    TOOL_POLICY_ACTION_REQUIRE_CONFIRM,
    TOOL_RESULT_STATUS_ERROR,
    TOOL_RESULT_STATUS_OK,
)


@dataclass
class _RegisteredMethod:
    plugin_id: str
    plugin_version: str
    tool: ToolDefinition
    method: ToolMethod


class ToolRuntime:
    """V1 plugin runtime for method-based tool execution."""

    def __init__(
        self,
        *,
        config: Optional[Dict[str, Any]] = None,
        artifact_sink: Optional[ArtifactSink] = None,
        artifactctl: Any | None = None,
        event_sink: Optional[EventSink] = None,
        policy_hook: Optional[PolicyHook] = None,
        logger: Any = None,
        artifact_inline_threshold_bytes: int = 16 * 1024,
        default_timeout_s: Optional[float] = None,
    ) -> None:
        self.config = config or {}
        if artifact_sink is not None:
            self.artifact_sink = artifact_sink
        elif artifactctl is not None:
            self.artifact_sink = CASArtifactSink(artifactctl=artifactctl)
        else:
            self.artifact_sink = MemoryArtifactSink()
        self.event_sink: EventSink = event_sink or NullEventSink()
        self.policy_hook = policy_hook
        self.logger = logger
        self.default_timeout_s = default_timeout_s
        self.artifact_inline_threshold_bytes = int(artifact_inline_threshold_bytes)
        if self.artifact_inline_threshold_bytes <= 0:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                "artifact_inline_threshold_bytes must be > 0",
                {"value": self.artifact_inline_threshold_bytes},
            )

        self._plugins: Dict[str, ToolPlugin] = {}
        self._tools: Dict[str, Tuple[str, str, ToolDefinition]] = {}
        self._methods: Dict[Tuple[str, str], _RegisteredMethod] = {}

    def register(self, plugin: ToolPlugin) -> None:
        if plugin.plugin_id in self._plugins:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Plugin already registered: {plugin.plugin_id}",
                {"plugin_id": plugin.plugin_id},
            )

        plugin_cfg = self._plugin_config(plugin.plugin_id)
        plugin.validate_config(plugin_cfg)
        plugin.init(self)

        added_tools: List[str] = []
        added_methods: List[Tuple[str, str]] = []

        try:
            tools = plugin.get_tools()
            if not tools:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    f"Plugin '{plugin.plugin_id}' did not provide any tools",
                    {"plugin_id": plugin.plugin_id},
                )

            for tool in tools:
                t_names, m_keys = self._register_tool(plugin, tool)
                added_tools.extend(t_names)
                added_methods.extend(m_keys)

            self._plugins[plugin.plugin_id] = plugin
        except Exception:
            # Roll back partial registration to keep runtime state consistent.
            for key in added_methods:
                self._methods.pop(key, None)
            for tool_name in added_tools:
                self._tools.pop(tool_name, None)
            try:
                plugin.shutdown()
            except Exception:
                pass
            raise

    def shutdown(self) -> None:
        for plugin in list(self._plugins.values()):
            try:
                plugin.shutdown()
            except Exception:
                continue

    def list_tools(self) -> List[ToolDescriptor]:
        rows: List[ToolDescriptor] = []
        for tool_name, (plugin_id, plugin_version, tool) in sorted(
            self._tools.items(), key=lambda item: item[0]
        ):
            rows.append(
                ToolDescriptor(
                    plugin_id=plugin_id,
                    plugin_version=plugin_version,
                    tool=tool_name,
                    methods=sorted(tool.methods.keys()),
                    capabilities=tool.capabilities,
                )
            )
        return rows

    def get_tool_schema(self, tool_name: str) -> Dict[str, Any]:
        if tool_name not in self._tools:
            raise KeyError(tool_name)
        _plugin_id, _plugin_version, tool = self._tools[tool_name]
        bundle = tool.schema()
        if not isinstance(bundle, ToolSchemaBundle):
            raise TypeError(  # allow-bare-raise: defensive type guard on schema bundle return type
                f"Tool '{tool_name}' returned invalid schema bundle type: {type(bundle).__name__}"
            )
        return bundle.model_dump()

    def plugin_health(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for plugin_id, plugin in sorted(
            self._plugins.items(), key=lambda item: item[0]
        ):
            health = plugin.healthcheck()
            if isinstance(health, HealthStatus):
                payload = health.model_dump()
            else:  # pragma: no cover - defensive compatibility path
                payload = {
                    "ok": False,
                    "details": {"error": "invalid healthcheck return type"},
                }
            rows.append(
                {
                    "plugin_id": plugin_id,
                    "version": plugin.version,
                    "healthy": bool(payload.get("ok", False)),
                    "health": payload,
                }
            )
        return rows

    def invoke(
        self, invocation: ToolInvocation | Dict[str, Any], ctx: ToolContext
    ) -> ToolResult:
        inv = (
            invocation
            if isinstance(invocation, ToolInvocation)
            else ToolInvocation.model_validate(invocation)
        )
        key = (inv.tool, inv.method)
        if key not in self._methods:
            err = ToolError(
                code="NOT_FOUND",
                message=f"Unknown tool.method: {inv.tool}.{inv.method}",
                retryable=False,
                details={"tool": inv.tool, "method": inv.method},
            )
            self._emit_failed(inv, ctx, metrics={"duration_ms": 0}, error=err)
            return ToolResult(
                status=TOOL_RESULT_STATUS_ERROR, error=err, metrics={"duration_ms": 0}
            )

        reg = self._methods[key]
        ctx.runtime = self
        if ctx.event_sink is None:
            ctx.event_sink = self.event_sink
        if ctx.artifact_sink is None:
            ctx.artifact_sink = self.artifact_sink
        if ctx.logger is None:
            ctx.logger = self.logger

        policy = self._evaluate_policy(inv, ctx, reg.tool.capabilities)
        if policy.action != TOOL_POLICY_ACTION_ALLOW:
            err_code = (
                "POLICY_DENIED"
                if policy.action == TOOL_POLICY_ACTION_DENY
                else TOOL_ERROR_CONFIRM_REQUIRED
            )
            details = dict(policy.details)
            result_data: Dict[str, Any] = {}
            confirm_request = details.get("confirm_request")
            if policy.action == TOOL_POLICY_ACTION_REQUIRE_CONFIRM and isinstance(
                confirm_request, dict
            ):
                result_data["confirm_request"] = confirm_request
            err = ToolError(
                code=err_code,
                message=policy.reason or "Invocation rejected by policy hook",
                retryable=False,
                details=details,
            )
            self._emit_failed(inv, ctx, metrics={"duration_ms": 0}, error=err)
            return ToolResult(
                status=TOOL_RESULT_STATUS_ERROR,
                error=err,
                metrics={"duration_ms": 0},
                data=result_data,
            )

        self._emit_requested(inv, ctx)
        started = time.perf_counter()
        timeout_s = (
            inv.timeout_s if inv.timeout_s is not None else self.default_timeout_s
        )

        try:
            if timeout_s is None:
                raw_result = reg.method.run(inv.args, ctx)
            else:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(reg.method.run, inv.args, ctx)
                    raw_result = future.result(timeout=float(timeout_s))
        except FuturesTimeoutError:
            metrics = {
                "duration_ms": self._duration_ms(started),
                "timeout_s": timeout_s,
            }
            err = ToolError(
                code="TIMEOUT",
                message=f"Invocation timed out after {timeout_s} seconds",
                retryable=True,
                details={"timeout_s": timeout_s},
            )
            self._emit_failed(inv, ctx, metrics=metrics, error=err)
            return ToolResult(
                status=TOOL_RESULT_STATUS_ERROR, error=err, metrics=metrics
            )
        except Exception as exc:
            metrics = {"duration_ms": self._duration_ms(started)}
            err = ToolError(
                code="REMOTE_ERROR",
                message=f"{type(exc).__name__}: {exc}",
                retryable=False,
                details={"exception_type": type(exc).__name__},
            )
            self._emit_failed(inv, ctx, metrics=metrics, error=err)
            return ToolResult(
                status=TOOL_RESULT_STATUS_ERROR, error=err, metrics=metrics
            )

        result = self._normalize_result(raw_result)
        result.metrics = {"duration_ms": self._duration_ms(started), **result.metrics}
        result = self._externalize_large_outputs(invocation=inv, result=result, ctx=ctx)

        if result.status == TOOL_RESULT_STATUS_OK:
            self._emit_completed(inv, ctx, result)
            return result

        self._emit_failed(inv, ctx, metrics=result.metrics, error=result.error)
        return result

    def _register_tool(
        self, plugin: ToolPlugin, tool: ToolDefinition
    ) -> Tuple[List[str], List[Tuple[str, str]]]:
        if not tool.name:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Plugin '{plugin.plugin_id}' registered tool with empty name",
                {"plugin_id": plugin.plugin_id},
            )
        if not tool.methods:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Tool '{tool.name}' from plugin '{plugin.plugin_id}' has no methods",
                {"plugin_id": plugin.plugin_id, "tool": tool.name},
            )
        if tool.name in self._tools:
            owner, _, _ = self._tools[tool.name]
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Duplicate tool '{tool.name}' from plugin '{plugin.plugin_id}', already owned by '{owner}'",
                {"plugin_id": plugin.plugin_id, "tool": tool.name, "owner": owner},
            )

        self._tools[tool.name] = (plugin.plugin_id, plugin.version, tool)
        method_keys: List[Tuple[str, str]] = []
        for method_name, method in tool.methods.items():
            if not method_name:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    f"Tool '{tool.name}' from plugin '{plugin.plugin_id}' has empty method name",
                    {"plugin_id": plugin.plugin_id, "tool": tool.name},
                )
            key = (tool.name, method_name)
            if key in self._methods:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    f"Duplicate method registration for '{tool.name}.{method_name}'",
                    {"tool": tool.name, "method": method_name},
                )
            self._methods[key] = _RegisteredMethod(
                plugin_id=plugin.plugin_id,
                plugin_version=plugin.version,
                tool=tool,
                method=method,
            )
            method_keys.append(key)
        return [tool.name], method_keys

    def _plugin_config(self, plugin_id: str) -> Dict[str, Any]:
        plugins_cfg = self.config.get("plugins", {})
        if not isinstance(plugins_cfg, dict):
            return {}
        value = plugins_cfg.get(plugin_id, {})
        return value if isinstance(value, dict) else {}

    def _evaluate_policy(
        self, inv: ToolInvocation, ctx: ToolContext, caps: ToolCapabilities
    ) -> PolicyDecision:
        if self.policy_hook is None:
            return PolicyDecision(
                action=TOOL_POLICY_ACTION_ALLOW, reason="No policy hook configured"
            )
        decision = self.policy_hook.check(invocation=inv, ctx=ctx, capabilities=caps)
        if decision.action not in (
            TOOL_POLICY_ACTION_ALLOW,
            TOOL_POLICY_ACTION_DENY,
            TOOL_POLICY_ACTION_REQUIRE_CONFIRM,
        ):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Invalid policy action: {decision.action}",
                {"action": str(decision.action)},
            )
        return decision

    def _normalize_result(self, value: Any) -> ToolResult:
        if isinstance(value, ToolResult):
            return value
        if isinstance(value, dict):
            if "status" in value:
                return ToolResult.model_validate(value)
            return ToolResult(status=TOOL_RESULT_STATUS_OK, data=dict(value))
        raise TypeError(  # allow-bare-raise: defensive type guard on plugin method return type
            f"Method returned unsupported result type: {type(value).__name__}"
        )

    def _externalize_large_outputs(
        self, *, invocation: ToolInvocation, result: ToolResult, ctx: ToolContext
    ) -> ToolResult:
        if result.stdout is not None:
            result = self._externalize_text_field(
                invocation=invocation,
                result=result,
                field_name="stdout",
                value=result.stdout,
                ctx=ctx,
            )
        if result.stderr is not None:
            result = self._externalize_text_field(
                invocation=invocation,
                result=result,
                field_name="stderr",
                value=result.stderr,
                ctx=ctx,
            )
        return result

    def _externalize_text_field(
        self,
        *,
        invocation: ToolInvocation,
        result: ToolResult,
        field_name: str,
        value: str,
        ctx: ToolContext,
    ) -> ToolResult:
        payload = value.encode("utf-8", errors="replace")
        if len(payload) <= self.artifact_inline_threshold_bytes:
            return result
        if ctx.artifact_sink is None:
            return result

        name = f"{invocation.tool}.{invocation.method}.{field_name}.txt"
        ref = ctx.artifact_sink.put_bytes(
            name=name,
            content=payload,
            kind="text",
            meta={"mime": "text/plain", "size": len(payload)},
        )
        result.artifacts.append(ref)
        preview = payload[: self.artifact_inline_threshold_bytes].decode(
            "utf-8", errors="replace"
        )
        preview += f"\n...[truncated, artifact_ref={ref.ref}]"
        if field_name == "stdout":
            result.stdout = preview
        else:
            result.stderr = preview
        result.metrics[f"{field_name}_bytes"] = len(payload)
        return result

    def _emit_requested(self, inv: ToolInvocation, ctx: ToolContext) -> None:
        payload = self._base_event_payload(inv, ctx)
        payload["args_sanitized"] = self._sanitize_args(inv.args)
        self._emit(
            f"tool.{inv.tool}.{inv.method}.requested",
            payload,
            sink=ctx.event_sink,
            logger=ctx.logger,
        )

    def _emit_completed(
        self, inv: ToolInvocation, ctx: ToolContext, result: ToolResult
    ) -> None:
        payload = self._base_event_payload(inv, ctx)
        payload["args_sanitized"] = self._sanitize_args(inv.args)
        payload["artifacts"] = [artifact.model_dump() for artifact in result.artifacts]
        payload["metrics"] = dict(result.metrics)
        self._emit(
            f"tool.{inv.tool}.{inv.method}.completed",
            payload,
            sink=ctx.event_sink,
            logger=ctx.logger,
        )

    def _emit_failed(
        self,
        inv: ToolInvocation,
        ctx: ToolContext,
        metrics: Dict[str, Any],
        error: ToolError,
    ) -> None:
        payload = self._base_event_payload(inv, ctx)
        payload["args_sanitized"] = self._sanitize_args(inv.args)
        payload["artifacts"] = []
        payload["metrics"] = dict(metrics)
        payload["error"] = error.model_dump()
        self._emit(
            f"tool.{inv.tool}.{inv.method}.failed",
            payload,
            sink=ctx.event_sink,
            logger=ctx.logger,
        )

    def _base_event_payload(
        self, inv: ToolInvocation, ctx: ToolContext
    ) -> Dict[str, Any]:
        payload = {
            "trace_id": ctx.trace_id,
            "session_id": ctx.session_id,
            "agent_id": ctx.agent_id,
            "invocation_id": inv.invocation_id,
            "tool": inv.tool,
            "method": inv.method,
        }
        orchestration = ctx.extras.get("orchestration")
        if isinstance(orchestration, dict):
            payload.update(
                {
                    str(key): value
                    for key, value in orchestration.items()
                    if str(key or "").strip()
                }
            )
        return payload

    def _emit(
        self,
        event_name: str,
        payload: Dict[str, Any],
        *,
        sink: Optional[EventSink] = None,
        logger: Any = None,
    ) -> None:
        target_sink = sink or self.event_sink
        try:
            target_sink.emit(event_name=event_name, payload=payload)
        except (
            Exception
        ) as exc:  # pragma: no cover - non-critical observability failure path
            target_logger = (
                logger
                or self.logger
                or logging.getLogger("openminion.modules.tool.runtime.plugins")
            )
            try:
                target_logger.warning("event sink emit failed: %s", exc)
            except Exception:
                pass

    def _sanitize_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in args.items():
            low = key.lower()
            if any(token in low for token in ("token", "secret", "password", "key")):
                out[key] = "[REDACTED]"
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[key] = value
                continue
            if isinstance(value, dict):
                try:
                    obj_text = json.dumps(value, sort_keys=True)
                    out[key] = {"_type": "object", "sha256": self._sha(obj_text)}
                except Exception:
                    out[key] = {"_type": "object", "size": len(value)}
                continue
            if isinstance(value, list):
                out[key] = {"_type": "array", "size": len(value)}
                continue
            out[key] = {"_type": type(value).__name__}
        return out

    def _sha(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _duration_ms(self, started: float) -> int:
        return int((time.perf_counter() - started) * 1000)


class AllowAllPolicyHook:
    """Default permissive policy hook for local development and tests."""

    def check(
        self,
        *,
        invocation: ToolInvocation,
        ctx: ToolContext,
        capabilities: ToolCapabilities,
    ) -> PolicyDecision:
        del invocation, ctx, capabilities
        return PolicyDecision(action=TOOL_POLICY_ACTION_ALLOW, reason="allow-all hook")


class DenyHighRiskWithoutTagPolicyHook:
    """
    Example policy hook:
    denies high-risk operations unless invocation tags include `approved=true`.
    """

    def check(
        self,
        *,
        invocation: ToolInvocation,
        ctx: ToolContext,
        capabilities: ToolCapabilities,
    ) -> PolicyDecision:
        del ctx
        if (
            capabilities.risk_level == "high"
            and invocation.tags.get("approved") != "true"
        ):
            return PolicyDecision(
                action=TOOL_POLICY_ACTION_REQUIRE_CONFIRM,
                reason="High-risk tool requires explicit approval tag",
                code=TOOL_ERROR_CONFIRM_REQUIRED,
                details={"required_tag": "approved=true"},
            )
        return PolicyDecision(action=TOOL_POLICY_ACTION_ALLOW, reason="policy passed")
