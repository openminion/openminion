"""Typed declarative Agent API backed by APIRuntime."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from openminion.api.runtime import APIRuntime

if TYPE_CHECKING:  # pragma: no cover
    from openminion.api.handoff import Handoff

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
MessageInput = str | BaseModel | dict[str, Any] | list[Any] | None


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


class Agent(Generic[InputT, OutputT]):
    """Typed declarative agent facade backed by :class:`APIRuntime`."""

    def __init__(
        self,
        *,
        instructions: str | None = None,
        output_type: type | None = None,
        runtime: APIRuntime | None = None,
        model: str | None = None,
        tools: list[str] | None = None,
        handoffs: list["Handoff"] | None = None,
        name: str | None = None,
    ) -> None:
        self.instructions = instructions
        self.output_type = output_type
        self.model = model
        self.tools = list(tools) if tools else []
        self.handoffs: list["Handoff"] = list(handoffs) if handoffs else []
        self.name = name or "agent"
        self._runtime: APIRuntime | None = runtime
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
            self._runtime = APIRuntime.from_config_path(None)
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

    def _build_payload(self, message: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": message}
        if self.instructions:
            payload["system_prompt"] = self.instructions
        if self.model:
            payload["override_model"] = self.model
        if self.tools:
            payload["allowed_tools"] = list(self.tools)
        return payload

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
        on_delta: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentRunResult[Any]:
        runtime = self._ensure_runtime()
        payload = self._build_payload(self._serialize_input(message))
        raw = runtime.run_turn(payload=payload, progress_callback=on_delta)
        reply_text = self._reply_text(raw)
        output = self._coerce_output(reply_text)
        return AgentRunResult(output=output, text=reply_text, raw=dict(raw or {}))

    def run(self, message: MessageInput) -> AgentRunResult[Any]:
        return self._run_once(message)

    def run_stream(
        self,
        message: MessageInput,
        *,
        on_delta: Callable[[dict[str, Any]], None] | None = None,
    ) -> AgentRunResult[Any]:
        """Synchronous streaming variant.

        ``on_delta`` is invoked for each progress event as it arrives. The
        final return value is the same :class:`AgentRunResult` shape that
        :meth:`run` returns. When ``on_delta`` is ``None`` this method is
        functionally equivalent to :meth:`run`.
        """
        return self._run_once(message, on_delta=on_delta)

    def close(self) -> None:
        if self._runtime is not None and self._owns_runtime:
            close = getattr(self._runtime, "close", None)
            if callable(close):
                close()
            self._runtime = None


def _extract_json_object(text: str) -> str | None:
    """Return the first balanced ``{...}`` span from ``text`` when present."""

    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for idx in range(start, len(text)):
        ch = text[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None
