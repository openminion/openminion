import hashlib
from pathlib import Path
from typing import Any

from openminion.base.logging import get_logger
from openminion.modules.tool.runtime.delegation import (
    A2ADelegateApi,
    A2ADelegateResult,
    map_a2a_delegate_result,
    run_a2a_job_lifecycle,
)
from openminion.services.runtime.constants import A2A_DELEGATE_DEFAULT_TIMEOUT_SECONDS

_LOG = get_logger("services.runtime.a2a_delegate")

# task.delegate maps to the A2A "delegate" method. The configured-agent
# handler reads the instruction from params["goal"]; "instruction" is mirrored
# for handlers that read it directly.
_DELEGATE_METHOD = "delegate"
_DEFAULT_TIMEOUT_SECONDS = A2A_DELEGATE_DEFAULT_TIMEOUT_SECONDS


class A2aRuntimeDelegateAdapter:
    """Tool-surface delegation over the in-process A2A runtime.

    Wraps any callable matching the A2actl/Local adapter
    ``call(*, command, session_id, trace_id) -> dict`` contract so it is
    trivially unit-testable with a fake call.
    """

    def __init__(self, *, a2a_call: Any, parent_agent_id: str = "") -> None:
        self._a2a_call = a2a_call
        self._parent_agent_id = str(parent_agent_id or "").strip()

    def _idempotency_key(self, *, target: str, instruction: str) -> str:
        digest = hashlib.sha256(
            f"{self._parent_agent_id}|{target}|{instruction}".encode("utf-8")
        ).hexdigest()[:32]
        return f"task-delegate:{digest}"

    def delegate(
        self,
        *,
        agent_id: str,
        instruction: str,
        timeout_seconds: int,
        mode: str = "sync",
    ) -> A2ADelegateResult:
        target = str(agent_id or "").strip()
        text = str(instruction or "").strip()
        normalized_mode = str(mode or "sync").strip().lower()
        try:
            timeout = int(timeout_seconds)
        except (TypeError, ValueError):
            timeout = _DEFAULT_TIMEOUT_SECONDS
        if timeout <= 0:
            timeout = _DEFAULT_TIMEOUT_SECONDS

        if not target or not text:
            return A2ADelegateResult(
                ok=False,
                status="failed",
                error_code="TASK_DELEGATE_INVALID_ARGS",
                error_message="task.delegate requires agent_id and instruction.",
                target_agent_id=target,
            )

        idem = self._idempotency_key(target=target, instruction=text)
        trace_id = idem
        session_id = f"task-delegate::{self._parent_agent_id or 'agent'}"
        command = {
            "command_id": idem,
            "target_agent_id": target,
            "method": _DELEGATE_METHOD,
            "expect_async": normalized_mode == "async",
            "params": {
                "goal": text,
                "instruction": text,
                "timeout_seconds": timeout,
                "mode": normalized_mode,
            },
            "timeout_ms": timeout * 1000,
            "idempotency_key": idem,
        }

        try:
            raw = self._a2a_call(
                command=command, session_id=session_id, trace_id=trace_id
            )
        except Exception as exc:  # noqa: BLE001 — map to typed result, never raise to model
            _LOG.warning("task.delegate A2A call failed: %s", exc)
            return A2ADelegateResult(
                ok=False,
                status="failed",
                error_code="A2A_RUNTIME_ERROR",
                error_message=str(exc),
                target_agent_id=target,
                trace_id=trace_id,
            )

        return map_a2a_delegate_result(
            raw,
            target=target,
            trace_id=trace_id,
            async_requested=normalized_mode == "async",
        )

    def status(self, *, task_id: str) -> A2ADelegateResult:
        return self._run_lifecycle("status", task_id, "poll_task")

    def resume(self, *, task_id: str) -> A2ADelegateResult:
        return self.status(task_id=task_id)

    def cancel(self, *, task_id: str) -> A2ADelegateResult:
        return self._run_lifecycle("cancel", task_id, "cancel_task")

    def _run_lifecycle(
        self, operation: str, task_id: str, method_name: str
    ) -> A2ADelegateResult:
        try:
            return run_a2a_job_lifecycle(
                caller=getattr(self._a2a_call, method_name, None),
                operation=operation,
                task_id=task_id,
                parent_agent_id=self._parent_agent_id,
            )
        except (RuntimeError, ValueError, TypeError, AttributeError, KeyError) as exc:
            _LOG.warning("task.delegate A2A %s failed: %s", operation, exc)
            return A2ADelegateResult(
                ok=False,
                status="failed",
                error_code="A2A_RUNTIME_ERROR",
                error_message=str(exc),
                task_id=str(task_id or "").strip(),
            )


def build_a2a_delegate_api(
    *,
    config: Any,
    home_root: str | Path | None,
    agent_id: str,
    env: Any = None,
    mode: str = "auto",
    runtime_resolver: Any = None,
) -> A2ADelegateApi | None:
    """Build a2a delegate api helper."""
    try:
        from openminion.modules.brain.adapters.factory.a2a import create_a2a_adapter
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("A2A delegate adapter factory import failed: %s", exc)
        return None
    try:
        a2actl = create_a2a_adapter(
            mode,
            home_root=home_root,
            config=config,
            agent_id=str(agent_id or "").strip() or None,
            env=env,
            runtime_resolver=runtime_resolver,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("A2A delegate adapter construction failed: %s", exc)
        return None
    call = getattr(a2actl, "call", None)
    if not callable(call):
        return None
    return A2aRuntimeDelegateAdapter(a2a_call=call, parent_agent_id=str(agent_id or ""))


__all__ = ["A2aRuntimeDelegateAdapter", "build_a2a_delegate_api"]
