from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
from types import SimpleNamespace
from typing import Any

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.services.runtime.a2a_delegate import build_a2a_delegate_api
from openminion.tools.agent.plugin import _h_task_delegate

_RESULT_MODE_ALIASES = frozenset({"result", "results"})
_STATUS_MODES = frozenset({"status", "resume", "cancel", *_RESULT_MODE_ALIASES})


@dataclass(frozen=True)
class AgentDelegateRequest:
    mode: str
    target_agent_id: str = ""
    instruction: str = ""
    task_id: str = ""
    timeout_seconds: int = 120

    def tool_args(self) -> dict[str, Any]:
        mode = normalize_delegate_mode(self.mode)
        return {
            "mode": mode,
            "agent_id": str(self.target_agent_id or "").strip(),
            "instruction": str(self.instruction or "").strip(),
            "task_id": str(self.task_id or "").strip(),
            "timeout_seconds": int(self.timeout_seconds or 120),
        }


def normalize_delegate_mode(mode: str) -> str:
    normalized = str(mode or "sync").strip().lower()
    if normalized in _RESULT_MODE_ALIASES:
        return "resume"
    return normalized or "sync"


def agent_delegate_usage() -> str:
    return (
        "Usage:\n"
        "  openminion agent delegate --target-agent-id <agent> --instruction <text>\n"
        "  openminion agent delegate --mode async --target-agent-id <agent> --instruction <text>\n"
        "  openminion agent delegate-status --task-id <task>\n"
        "  openminion agent delegate-result --task-id <task>\n"
        "  openminion agent delegate-cancel --task-id <task>\n"
        "\nCompatibility:\n"
        "  openminion agent-ctl delegate ... remains supported."
    )


def run_agent_delegate_request(
    *,
    config: Any,
    home_root: Any,
    parent_agent_id: str,
    request: AgentDelegateRequest,
    delegate_api: Any | None = None,
    runtime_resolver: Any | None = None,
    approval_callback: Any | None = None,
    workspace_root: str | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    seam = delegate_api
    if seam is None:
        runtime_env = getattr(getattr(config, "runtime", None), "env", None)
        seam = build_a2a_delegate_api(
            config=config,
            home_root=home_root,
            agent_id=parent_agent_id,
            env=dict(runtime_env or {}) if runtime_env else None,
            runtime_resolver=runtime_resolver,
            approval_callback=approval_callback,
        )
    if seam is None:
        return {
            "ok": False,
            "mode": normalize_delegate_mode(request.mode),
            "error": {
                "code": "DEPENDENCY_MISSING",
                "message": "A2A delegation is not configured for this runtime.",
                "details": {"reason_code": "task_delegate_seam_unavailable"},
            },
        }
    try:
        return dict(
            _h_task_delegate(
                request.tool_args(),
                SimpleNamespace(
                    a2a_delegate_api=seam,
                    workspace=Path(str(cwd or workspace_root or "").strip() or ".")
                    .expanduser()
                    .resolve(strict=False),
                ),
            )
        )
    except ToolRuntimeError as exc:
        return {
            "ok": False,
            "mode": normalize_delegate_mode(request.mode),
            "agent_id": request.target_agent_id,
            "task_id": request.task_id,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": dict(exc.details or {}),
            },
        }


def render_agent_delegate_result(payload: dict[str, Any]) -> str:
    if not bool(payload.get("ok", False)):
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code") or "ERROR")
        message = str(error.get("message") or "Delegation failed.")
        return f"Delegation failed [{code}]: {message}"

    lines = [
        "Delegation:",
        f"  mode      {payload.get('mode', '-')}",
        f"  status    {payload.get('status', '-')}",
    ]
    agent_id = str(payload.get("agent_id", "") or "").strip()
    task_id = str(payload.get("task_id", "") or "").strip()
    trace_id = str(payload.get("trace_id", "") or "").strip()
    content = str(payload.get("content", "") or "").strip()
    if agent_id:
        lines.append(f"  agent     {agent_id}")
    if task_id:
        lines.append(f"  task      {task_id}")
    if trace_id:
        lines.append(f"  trace     {trace_id}")
    if content:
        lines.extend(("", content))
    return "\n".join(lines)


def request_from_operator_args(args: Any) -> AgentDelegateRequest:
    action = str(getattr(args, "agent_command", "") or "").strip().lower()
    mode = str(getattr(args, "mode", "") or "").strip().lower()
    if action.startswith("delegate-"):
        mode = action.removeprefix("delegate-")
    if action == "delegate" and not mode:
        mode = "sync"
    return AgentDelegateRequest(
        mode=mode,
        target_agent_id=str(getattr(args, "target_agent_id", "") or "").strip(),
        instruction=str(getattr(args, "instruction", "") or "").strip(),
        task_id=str(getattr(args, "task_id", "") or "").strip(),
        timeout_seconds=int(getattr(args, "timeout_seconds", 120) or 120),
    )


def request_from_slash_args(args: str) -> AgentDelegateRequest:
    try:
        parts = shlex.split(str(args or ""))
    except ValueError as exc:
        raise ValueError(f"/delegate: {exc}") from exc
    if not parts:
        raise ValueError(
            "Usage: /delegate <agent> <instruction...> | "
            "/delegate async <agent> <instruction...> | "
            "/delegate status|result|resume|cancel <task-id>"
        )
    action = parts[0].lower()
    if action in {"status", "result", "resume", "cancel"}:
        if len(parts) != 2:
            raise ValueError(f"Usage: /delegate {action} <task-id>")
        return AgentDelegateRequest(mode=action, task_id=parts[1])
    mode = action if action in {"sync", "async"} else "sync"
    offset = 1 if action in {"sync", "async"} else 0
    if len(parts) <= offset + 1:
        raise ValueError("Usage: /delegate [sync|async] <agent> <instruction...>")
    return AgentDelegateRequest(
        mode=mode,
        target_agent_id=parts[offset],
        instruction=" ".join(parts[offset + 1 :]),
    )


def delegate_action_requires_task_id(action: str) -> bool:
    normalized = str(action or "").strip().lower()
    return normalized != "delegate" and normalized.removeprefix("delegate-") in _STATUS_MODES


__all__ = [
    "AgentDelegateRequest",
    "agent_delegate_usage",
    "delegate_action_requires_task_id",
    "normalize_delegate_mode",
    "render_agent_delegate_result",
    "request_from_operator_args",
    "request_from_slash_args",
    "run_agent_delegate_request",
]
