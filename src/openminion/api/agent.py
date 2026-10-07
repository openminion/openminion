"""Typed declarative Agent API backed by APIRuntime."""

from __future__ import annotations

from collections.abc import Awaitable
from contextlib import nullcontext
from dataclasses import dataclass, field
import json
from threading import Event
from types import TracebackType
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeVar, cast
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from openminion.api.runtime import APIRuntime
from openminion.modules.tool.registry import ToolRegistry

if TYPE_CHECKING:  # pragma: no cover
    from openminion.api.handoff import (
        DelegatedMemoryReadRequest,
        Handoff,
        SubagentRunContext,
    )

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
ToolInput = str | Callable[..., Any]
_ApprovalCallback = Callable[[str, dict[str, Any], str], bool | Awaitable[bool]]


class AgentOutputValidationError(ValueError):
    """Raised when the agent reply cannot be coerced into ``output_type``."""

    def __init__(
        self,
        message: str,
        *,
        raw_text: str,
        validation_error: ValidationError | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.validation_error = validation_error


@dataclass
class AgentRunResult(Generic[OutputT]):
    """Normalized result returned by :class:`Agent` runs."""

    output: OutputT
    text: str
    raw: dict[str, Any]
    run_id: str | None = None
    run_state: str | None = None
    session_id: str | None = None
    id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)


class Agent(Generic[InputT, OutputT]):
    """Typed declarative agent facade backed by :class:`APIRuntime`."""

    def __init__(
        self,
        *,
        instructions: str | None = None,
        output_type: type[OutputT] | None = None,
        runtime: APIRuntime | None = None,
        config_path: str | None = None,
        agent_id: str | None = None,
        model: str | None = None,
        tools: list[ToolInput] | None = None,
        forced_tools: list[str] | None = None,
        handoffs: list["Handoff"] | None = None,
        name: str | None = None,
        session_id: str | None = None,
        subagent_context: "SubagentRunContext | None" = None,
        delegated_memory_request: "DelegatedMemoryReadRequest | None" = None,
    ) -> None:
        if runtime is not None and config_path is not None:
            raise ValueError("runtime and config_path cannot be used together")
        self.instructions = instructions
        self.output_type = output_type
        self.agent_id = (agent_id or "").strip() or None
        self.model = model
        self.tools: list[str] = []
        self._tool_family_builders: list[Callable[[], Any]] = []
        for configured_tool in tools or []:
            if isinstance(configured_tool, str):
                self.tools.append(configured_tool)
                continue
            family_builder = getattr(configured_tool, "tool_family_spec", None)
            tool_decl = getattr(configured_tool, "tool_decl", None)
            tool_name = str(getattr(tool_decl, "name", "") or "").strip()
            if (
                not callable(configured_tool)
                or not callable(family_builder)
                or not tool_name
            ):
                raise TypeError(
                    "Agent tools must be registered tool names or functions decorated "
                    "with openminion.tool"
                )
            self._tool_family_builders.append(family_builder)
            if tool_name not in self.tools:
                self.tools.append(tool_name)
        self.forced_tools = list(forced_tools) if forced_tools else []
        self.handoffs: list["Handoff"] = list(handoffs) if handoffs else []
        self.name = name or "agent"
        self.session_id = (session_id or "").strip() or uuid4().hex
        self.subagent_context = subagent_context
        self.delegated_memory_request = delegated_memory_request
        self._runtime: APIRuntime | None = runtime
        self._config_path = config_path
        self._owns_runtime = runtime is None

        if self.handoffs:
            from openminion.api.handoff import build_delegate_tool

            self.handoff_tool_names: list[str] = [
                build_delegate_tool(h).name for h in self.handoffs
            ]
            for tname in self.handoff_tool_names:
                if tname not in self.tools:
                    self.tools.append(tname)
        else:
            self.handoff_tool_names = []

    def _ensure_runtime(self) -> APIRuntime:
        if self._runtime is None:
            self._runtime = APIRuntime.from_config_path(
                self._config_path, logging_mode="interactive"
            )
        return self._runtime

    def _serialize_input(self, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, BaseModel):
            return value.model_dump_json()
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=True, sort_keys=True)
        return str(value)

    def _build_payload(
        self,
        message: str,
        *,
        session_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message": message,
            "session_id": (session_id or "").strip() or self.session_id,
            "deliver": False,
        }
        if self.agent_id:
            payload["agent_id"] = self.agent_id
        if self.instructions:
            payload["override_system_prompt"] = self.instructions
        if self.model:
            payload["override_model"] = self.model
        if self.tools:
            payload["allowed_tools"] = list(self.tools)
        if self.forced_tools:
            payload["forced_tools"] = list(self.forced_tools)
        if timeout_seconds is not None:
            payload["timeout_seconds"] = timeout_seconds
        elif (
            self.subagent_context is not None
            and self.subagent_context.timeout_seconds is not None
        ):
            payload["timeout_seconds"] = self.subagent_context.timeout_seconds
        return payload

    def _register_tools_for_run(self, registry: ToolRegistry) -> list[str]:
        from openminion.api.handoff import build_delegate_family_spec
        from openminion.modules.tool.errors import ToolRuntimeError
        from openminion.modules.tool.framework import derive_tool_specs

        families = [builder() for builder in self._tool_family_builders]
        handoff_family = build_delegate_family_spec(self.handoffs)
        if handoff_family is not None:
            families.append(handoff_family)

        registered: list[str] = []
        try:
            for family in families:
                for spec in derive_tool_specs(family):
                    spec.prompt_visible_runtime_name = True
                    registry.add(spec)
                    registered.append(spec.name)
        except (ToolRuntimeError, TypeError, ValueError, AttributeError):
            for name in reversed(registered):
                registry.unregister(name)
            raise
        return registered

    @staticmethod
    def _unregister_tools(
        registry: ToolRegistry | None,
        tool_names: list[str],
    ) -> None:
        if registry is None:
            return
        for name in reversed(tool_names):
            registry.unregister(name)

    def _coerce_output(self, text: str) -> OutputT:
        if self.output_type is None or self.output_type is str:
            return cast(OutputT, text)
        if isinstance(self.output_type, type) and issubclass(
            self.output_type, BaseModel
        ):
            try:
                return cast(OutputT, self.output_type.model_validate_json(text))
            except ValidationError as exc:
                # Some providers wrap JSON answers in prose; recover the first
                # balanced object before failing validation.
                stripped = _extract_json_object(text)
                if stripped:
                    try:
                        return cast(
                            OutputT,
                            self.output_type.model_validate_json(stripped),
                        )
                    except ValidationError:
                        pass
                raise AgentOutputValidationError(
                    f"Reply did not validate against {self.output_type.__name__}: {exc}",
                    raw_text=text,
                    validation_error=exc,
                ) from exc
        try:
            return self.output_type(text)  # type: ignore[call-arg]
        except Exception as exc:  # noqa: BLE001
            raise AgentOutputValidationError(
                f"Reply could not be coerced to {self.output_type!r}: {exc}",
                raw_text=text,
            ) from exc

    def _run_once(
        self,
        message: InputT,
        *,
        session_id: str | None = None,
        on_delta: Callable[[object], None] | None = None,
        timeout_seconds: float | None = None,
        approval_callback: _ApprovalCallback | None = None,
        cancel_event: Event | None = None,
    ) -> AgentRunResult[OutputT]:
        runtime = self._ensure_runtime()
        payload = self._build_payload(
            self._serialize_input(message),
            session_id=session_id,
            timeout_seconds=timeout_seconds,
        )
        run_context = None
        delegated_grant_id = None
        temporary_tools = bool(self.handoffs or self._tool_family_builders)
        registry = getattr(runtime, "tools", None) if temporary_tools else None
        if temporary_tools and not isinstance(registry, ToolRegistry):
            raise TypeError(
                "Agent decorated tools and handoffs require APIRuntime.tools "
                "to be a ToolRegistry"
            )
        if self.subagent_context is not None:
            from openminion.api.handoff import materialize_subagent_run_context

            run_context = materialize_subagent_run_context(
                self.subagent_context,
                self.delegated_memory_request,
                action_policy=getattr(runtime, "action_policy", None),
            )
            self.subagent_context = run_context
            delegated_grant_id = run_context.memory_grant_id
        registration_scope = (
            registry.temporary_registration_lock
            if registry is not None
            else nullcontext()
        )
        with registration_scope:
            try:
                registered_tools = (
                    self._register_tools_for_run(registry)
                    if registry is not None
                    else []
                )
                try:
                    internal_approval_callback = None
                    if approval_callback is not None:

                        def internal_approval_callback(
                            tool_name: str,
                            args: dict[str, Any],
                            approval_id: str,
                            _policy_facts: dict[str, Any],
                        ) -> bool | Awaitable[bool]:
                            return approval_callback(tool_name, args, approval_id)

                    raw = runtime.run_turn(
                        payload=payload,
                        progress_callback=on_delta,
                        approval_callback=internal_approval_callback,
                        cancel_event=cancel_event,
                        trusted_subagent_context=run_context,
                    )
                finally:
                    self._unregister_tools(registry, registered_tools)
            finally:
                if delegated_grant_id is not None:
                    runtime.action_policy.revoke_grant(delegated_grant_id)
        if not isinstance(raw, dict) or "body" not in raw:
            raise RuntimeError("APIRuntime.run_turn() returned no canonical body")
        reply_text = str(raw["body"] or "")
        raw_payload = dict(raw)
        metadata_value = raw_payload.get("metadata", {})
        stats_value = raw_payload.get("stats", {})
        if not isinstance(metadata_value, dict) or not isinstance(stats_value, dict):
            raise RuntimeError("APIRuntime.run_turn() returned invalid result facts")
        output = self._coerce_output(reply_text)
        return AgentRunResult(
            output=output,
            text=reply_text,
            raw=raw_payload,
            id=str(raw_payload.get("id") or "").strip() or None,
            session_id=str(raw_payload.get("session_id") or "").strip() or None,
            run_id=str(raw_payload.get("run_id") or "").strip() or None,
            run_state=str(raw_payload.get("run_state") or "").strip() or None,
            metadata=dict(metadata_value),
            stats=dict(stats_value),
        )

    def run(
        self,
        message: InputT,
        *,
        session_id: str | None = None,
        timeout_seconds: float | None = None,
        approval_callback: _ApprovalCallback | None = None,
        cancel_event: Event | None = None,
    ) -> AgentRunResult[OutputT]:
        return self._run_once(
            message,
            session_id=session_id,
            timeout_seconds=timeout_seconds,
            approval_callback=approval_callback,
            cancel_event=cancel_event,
        )

    def run_stream(
        self,
        message: InputT,
        *,
        session_id: str | None = None,
        on_delta: Callable[[object], None] | None = None,
        timeout_seconds: float | None = None,
        approval_callback: _ApprovalCallback | None = None,
        cancel_event: Event | None = None,
    ) -> AgentRunResult[OutputT]:
        """Run one turn while forwarding progress events to ``on_delta``."""
        return self._run_once(
            message,
            session_id=session_id,
            on_delta=on_delta,
            timeout_seconds=timeout_seconds,
            approval_callback=approval_callback,
            cancel_event=cancel_event,
        )

    def close(self) -> None:
        if self._runtime is not None and self._owns_runtime:
            self._runtime.close()
            self._runtime = None

    def __enter__(self) -> Agent[InputT, OutputT]:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()


def _extract_json_object(text: str) -> str | None:
    """Return the first decodable JSON object span from ``text``."""

    decoder = json.JSONDecoder()
    for start, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return text[start : start + end]
    return None
