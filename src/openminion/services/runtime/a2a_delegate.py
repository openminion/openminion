import hashlib
from pathlib import Path
from typing import Any

from openminion.base.logging import get_logger
from openminion.modules.tool.runtime.delegation import (
    A2ADelegateApi,
    A2ADelegateResult,
)
from openminion.services.runtime.constants import A2A_DELEGATE_DEFAULT_TIMEOUT_SECONDS

_LOG = get_logger("services.runtime.a2a_delegate")

# task.delegate maps to the A2A "delegate" method. The configured-agent
# handler reads the instruction from params["goal"]; "instruction" is mirrored
# for handlers that read it directly.
_DELEGATE_METHOD = "delegate"
_DEFAULT_TIMEOUT_SECONDS = A2A_DELEGATE_DEFAULT_TIMEOUT_SECONDS


def _is_success_status(status: Any) -> bool:
    from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_SUCCESS

    return str(status or "").strip() == str(BRAIN_ACTION_STATUS_SUCCESS)


def _is_running_status(status: Any) -> bool:
    from openminion.modules.brain.constants import (
        BRAIN_JOB_STATUS_PENDING,
        BRAIN_JOB_STATUS_RUNNING,
    )

    token = str(status or "").strip()
    return token in {str(BRAIN_JOB_STATUS_RUNNING), str(BRAIN_JOB_STATUS_PENDING)}


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
    ) -> A2ADelegateResult:
        target = str(agent_id or "").strip()
        text = str(instruction or "").strip()
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
            "params": {
                "goal": text,
                "instruction": text,
                "timeout_seconds": timeout,
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

        return self._map_result(raw, target=target, trace_id=trace_id)

    @staticmethod
    def _map_result(raw: Any, *, target: str, trace_id: str) -> A2ADelegateResult:
        payload = raw if isinstance(raw, dict) else {}
        status = payload.get("status")
        summary = str(payload.get("summary", "") or "").strip()
        outputs = payload.get("outputs")
        normalized_outputs = dict(outputs) if isinstance(outputs, dict) else {}
        task_id = str(payload.get("task_id", "") or "").strip()

        if _is_success_status(status):
            return A2ADelegateResult(
                ok=True,
                status="success",
                content=summary,
                target_agent_id=target,
                trace_id=trace_id,
                task_id=task_id,
                outputs=normalized_outputs,
            )
        if _is_running_status(status):
            # v1 is synchronous; surface async as a typed, honest result.
            return A2ADelegateResult(
                ok=False,
                status="running",
                content=summary or "Delegated A2A job is still running.",
                error_code="A2A_DELEGATE_ASYNC_UNSUPPORTED",
                error_message=(
                    "task.delegate is synchronous in v1; the target returned an "
                    "async job. Use the brain delegation path for async work."
                ),
                target_agent_id=target,
                trace_id=trace_id,
                task_id=task_id,
                outputs=normalized_outputs,
            )

        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        return A2ADelegateResult(
            ok=False,
            status="failed",
            content=summary,
            error_code=str(error.get("code") or "A2A_DELEGATE_FAILED"),
            error_message=str(
                error.get("message") or summary or "A2A delegation failed."
            ),
            target_agent_id=target,
            trace_id=trace_id,
            task_id=task_id,
            outputs=normalized_outputs,
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
