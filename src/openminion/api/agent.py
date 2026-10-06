"""Typed declarative Agent API backed by APIRuntime."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import json
from types import TracebackType
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from openminion.api.runtime import APIRuntime

if TYPE_CHECKING:  # pragma: no cover
    from openminion.api.handoff import (
        DelegatedMemoryReadRequest,
        Handoff,
        SubagentRunContext,
    )

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
MessageInput = str | BaseModel | dict[str, Any] | list[Any] | None
ToolInput = str | Callable[..., Any]


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


class Agent(Generic[InputT, OutputT]):
    """Typed declarative agent facade backed by :class:`APIRuntime`."""

    def __init__(
        self,
        *,
        instructions: str | None = None,
        output_type: type | None = None,
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

    def _serialize_input(self, value: Any) -> str:
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
        if (
            self.subagent_context is not None
            and self.subagent_context.timeout_seconds is not None
        ):
            payload["timeout_seconds"] = self.subagent_context.timeout_seconds
        return payload

    def _register_tools_for_run(self, runtime: APIRuntime) -> list[str]:
        if not self.handoffs and not self._tool_family_builders:
            return []
        registry = getattr(runtime, "tools", None)
        add_tool = getattr(registry, "add", None)
        unregister_tool = getattr(registry, "unregister", None)
        if not callable(add_tool) or not callable(unregister_tool):
            return []

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
                    add_tool(spec)
                    registered.append(spec.name)
        except (ToolRuntimeError, TypeError, ValueError, AttributeError):
            for name in reversed(registered):
                unregister_tool(name)
            raise
        return registered

    @staticmethod
    def _unregister_tools(runtime: APIRuntime, tool_names: list[str]) -> None:
        if not tool_names:
            return
        registry = getattr(runtime, "tools", None)
        unregister_tool = getattr(registry, "unregister", None)
        if not callable(unregister_tool):
            return
        for name in reversed(tool_names):
            unregister_tool(name)

    def _coerce_output(self, text: str) -> Any:
        if self.output_type is None or self.output_type is str:
            return text
        if isinstance(self.output_type, type) and issubclass(
            self.output_type, BaseModel
        ):
            try:
                return self.output_type.model_validate_json(text)
            except ValidationError as exc:
                # Some providers wrap JSON answers in prose; recover the first
                # balanced object before failing validation.
                stripped = _extract_json_object(text)
                if stripped:
                    try:
                        return self.output_type.model_validate_json(stripped)
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

    def _reply_text(self, raw: Any) -> str:
        if not isinstance(raw, dict):
            return ""
        return str(raw.get("body") or raw.get("text") or raw.get("reply") or "")

    def _run_once(
        self,
        message: MessageInput,
        *,
        session_id: str | None = None,
        on_delta: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentRunResult[Any]:
        runtime = self._ensure_runtime()
        payload = self._build_payload(
            self._serialize_input(message),
            session_id=session_id,
        )
        run_context = None
        delegated_grant_id = None
        if self.subagent_context is not None:
            from openminion.api.handoff import materialize_subagent_run_context

            run_context = materialize_subagent_run_context(
                self.subagent_context,
                self.delegated_memory_request,
                action_policy=getattr(runtime, "action_policy", None),
            )
            self.subagent_context = run_context
            delegated_grant_id = run_context.memory_grant_id
        registry = getattr(runtime, "tools", None)
        registration_lock = getattr(registry, "temporary_registration_lock", None)
        with registration_lock or nullcontext():
            try:
                registered_tools = self._register_tools_for_run(runtime)
                try:
                    raw = runtime.run_turn(
                        payload=payload,
                        progress_callback=on_delta,
                        trusted_subagent_context=run_context,
                    )
                finally:
                    self._unregister_tools(runtime, registered_tools)
            finally:
                if delegated_grant_id is not None:
                    runtime.action_policy.revoke_grant(delegated_grant_id)
        reply_text = self._reply_text(raw)
        output = self._coerce_output(reply_text)
        raw_payload = dict(raw or {})
        return AgentRunResult(
            output=output,
            text=reply_text,
            raw=raw_payload,
            session_id=str(raw_payload.get("session_id") or "").strip() or None,
            run_id=str(raw_payload.get("run_id") or "").strip() or None,
            run_state=str(raw_payload.get("run_state") or "").strip() or None,
        )

    def run(
        self,
        message: MessageInput,
        *,
        session_id: str | None = None,
    ) -> AgentRunResult[Any]:
        return self._run_once(message, session_id=session_id)

    def run_stream(
        self,
        message: MessageInput,
        *,
        session_id: str | None = None,
        on_delta: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentRunResult[Any]:
        """Run one turn while forwarding progress events to ``on_delta``."""
        return self._run_once(message, session_id=session_id, on_delta=on_delta)

    def close(self) -> None:
        if self._runtime is not None and self._owns_runtime:
            close = getattr(self._runtime, "close", None)
            if callable(close):
                close()
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
