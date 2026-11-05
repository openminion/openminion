"""Bounded read/redecide loop owned by entry, without executing external tools."""

import json
from typing import Any
from uuid import uuid4
from openminion.modules.llm.schemas import Message
from .task_context import TaskContextBudgetError, input_digest
from .task_search import HISTORY_TOOL, search_task_history


class TaskHistoryEntryRuntime:
    def __init__(self, runtime: Any, runner: Any, state: Any, logger: Any, user_input: str | None) -> None:
        self.runtime, self.runner, self.state, self.logger = runtime, runner, state, logger
        self.user_input = str(user_input or "")
        task = state.resolved_task_context
        self.binding = (task.task_id, task.revision) if task else None
        self.history_reads: dict[str, Any] = {}
        self.exchanges: list[Message] = []
        self.lookups = 0

    def complete(self, *, messages: list[Message], **request: Any) -> Any:
        while True:
            self._check_owner()
            response = self.runtime.complete(messages=[*messages, *self.exchanges], **request)
            try:
                self._check_owner()
            except TaskContextBudgetError:
                self._debit(response, request)
                raise
            calls = response.tool_calls or []
            history = [call for call in calls if call.name == HISTORY_TOOL]
            if not history:
                return response
            self._debit(response, request)
            if len(calls) != 1 or "task_update" in history[0].arguments:
                raise TaskContextBudgetError("Read task history alone before selecting an action; no action was executed.")
            if self.lookups >= 3:
                raise TaskContextBudgetError("The history lookup limit was reached. Please specify which task or condition to restore.")
            self.lookups += 1
            call = history[0].model_copy(deep=True)
            call.id = call.id or uuid4().hex
            result = self._search(call.arguments)
            encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            if len(encoded) > 32768:
                result = {"status": "too_large", "instruction": "Narrow the query or lower limit; no entries were authorized."}
                encoded = json.dumps(result)
            else:
                for item in result.get("entries", []):
                    self.history_reads[item["reference"]] = {"entry": item, "binding": self.binding, "input": input_digest(self.user_input)}
            self.exchanges.extend([
                Message(role="assistant", content=response.output_text or "", tool_calls=[call]),
                Message(role="tool", name=HISTORY_TOOL, tool_call_id=call.id, content=encoded,
                        tool_output=result, tool_status="success"),
            ])

    def _check_owner(self) -> None:
        task = self.state.resolved_task_context
        current = (task.task_id, task.revision) if task else None
        if current != self.binding:
            raise TaskContextBudgetError("The task changed while reading history; retry with its current conditions.")
        if self.state.llm_calls_used >= self.state.llm_calls_max:
            raise TaskContextBudgetError("The model call budget was exhausted while reading history.")

    def _search(self, arguments: Any) -> dict[str, Any]:
        if not self.user_input.strip():
            return {"status": "unavailable", "reason": "History lookup requires a user request"}
        try:
            return search_task_history(self.runner.session_api, self.state, arguments)
        except (ValueError, TypeError, OSError, RuntimeError):
            return {"status": "unavailable", "reason": "Invalid query or committed history unavailable; do not guess missing values."}

    def _debit(self, response: Any, request: dict[str, Any]) -> None:
        from openminion.modules.brain.loop.orchestration import _emit_entry_response
        from .task_entry import debit_entry_response, emit_entry_identity_audit
        call_id = "history:" + uuid4().hex
        self.logger.emit("llm.call.started", {"llm_call_id": call_id, "purpose": "entry", "model": request.get("model")}, trace_id=self.state.trace_id)
        emit_entry_identity_audit(self.logger, self.state, self.runner, call_id)
        usage = _emit_entry_response(self.logger, self.state, response, call_id=call_id,
                                     metrics={"model": request.get("model"), "history_lookup": True}, attempt=0)
        debit_entry_response(self.runner, self.state, usage, self.logger)
