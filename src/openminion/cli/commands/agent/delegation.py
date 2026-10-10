from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.services.runtime.a2a_delegate import build_a2a_delegate_api
from openminion.tools.agent.plugin import _h_task_delegate

_RESULT_MODE_ALIASES = frozenset({"result", "results"})
_STATUS_MODES = frozenset({"status", "resume", "cancel", *_RESULT_MODE_ALIASES})
_DELEGATE_START_USAGE = (
    "Usage: /delegate [sync|async] [--child-permission-mode readonly] "
    "<agent> <instruction...>"
)


@dataclass(frozen=True)
class AgentDelegateRequest:
    mode: str
    target_agent_id: str = ""
    instruction: str = ""
    child_permission_mode: str = ""
    task_id: str = ""
    timeout_seconds: int = 120
    child_artifact: dict[str, Any] | None = None
    workspace_root: str = ""
    review_criteria: tuple[str, ...] = ()
    repository_instructions: str = ""
    limit: int = 20

    def tool_args(self) -> dict[str, Any]:
        return {
            "mode": normalize_delegate_mode(self.mode),
            "agent_id": self.target_agent_id.strip(),
            "instruction": self.instruction.strip(),
            "child_permission_mode": self.child_permission_mode.strip(),
            "task_id": self.task_id.strip(),
            "timeout_seconds": self.timeout_seconds or 120,
            "child_artifact": dict(self.child_artifact or {}),
            "workspace_root": self.workspace_root.strip(),
            "review_criteria": list(self.review_criteria),
            "repository_instructions": self.repository_instructions.strip(),
            "limit": max(1, min(int(self.limit), 200)),
        }


def normalize_delegate_mode(mode: str) -> str:
    normalized = (mode or "sync").strip().lower()
    if normalized in _RESULT_MODE_ALIASES:
        return "result"
    return normalized or "sync"


def agent_delegate_usage() -> str:
    return (
        "Usage:\n"
        "  openminion agent delegate --target-agent-id <agent> --instruction <text>\n"
        "  openminion agent delegate --child-permission-mode readonly "
        "--target-agent-id <agent> --instruction <text>\n"
        "  openminion agent delegate --mode async --target-agent-id <agent> --instruction <text>\n"
        "  openminion agent delegate-list [--limit 20]\n"
        "  openminion agent delegate-status --task-id <task>\n"
        "  openminion agent delegate-result --task-id <task>\n"
        "  openminion agent delegate-cancel --task-id <task>\n"
        "  /delegate review '<review-request-json>'\n"
        "  /delegate accept|reject '<child-artifact-json>'\n"
        "\nCompatibility:\n"
        "  openminion agent-ctl delegate ... remains supported.\n"
        "  delegate-resume is recognized but does not resume completed or stopped work."
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
    artifactctl: Any | None = None,
    session_id: str = "",
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
        tool_args = request.tool_args()
        bound_session_id = str(session_id or f"operator:{parent_agent_id}").strip()
        if tool_args["mode"] == "accept" and not tool_args["workspace_root"]:
            tool_args["workspace_root"] = str(
                Path((cwd or workspace_root or "").strip() or ".")
                .expanduser()
                .resolve(strict=False)
            )
        return dict(
            _h_task_delegate(
                tool_args,
                SimpleNamespace(
                    a2a_delegate_api=seam,
                    artifactctl=artifactctl,
                    session_id=bound_session_id,
                    telemetry_session_id=bound_session_id,
                    workspace=Path((cwd or workspace_root or "").strip() or ".")
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
    child_permission_mode = str(payload.get("child_permission_mode", "") or "").strip()
    content = str(payload.get("content", "") or "").strip()
    if agent_id:
        lines.append(f"  agent     {agent_id}")
    if task_id:
        lines.append(f"  task      {task_id}")
    if trace_id:
        lines.append(f"  trace     {trace_id}")
    if child_permission_mode:
        lines.append(f"  child permissions  {child_permission_mode}")
    if content:
        lines.extend(("", content))
    outputs = payload.get("outputs")
    jobs = outputs.get("jobs") if isinstance(outputs, dict) else None
    if isinstance(jobs, list):
        for job in jobs:
            if not isinstance(job, dict):
                continue
            lines.append(
                "  "
                f"{job.get('task_id', '-')}  {job.get('state', '-')}  "
                f"{job.get('agent_id', '-')}  trace={job.get('trace_id', '-')}"
            )
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
        child_permission_mode=str(
            getattr(args, "child_permission_mode", "") or ""
        ).strip(),
        task_id=str(getattr(args, "task_id", "") or "").strip(),
        timeout_seconds=int(getattr(args, "timeout_seconds", 120) or 120),
        limit=int(getattr(args, "limit", 20) or 20),
    )


def _start_request_from_slash_args(
    *,
    raw: str,
    action: str,
    remainder: str,
) -> AgentDelegateRequest:
    mode = action if action in {"sync", "async"} else "sync"
    delegate_args = remainder if action in {"sync", "async"} else raw
    child_permission_mode = ""
    if delegate_args.startswith("--child-permission-mode "):
        permission_parts = delegate_args.split(maxsplit=2)
        if len(permission_parts) != 3 or permission_parts[1] != "readonly":
            raise ValueError("/delegate --child-permission-mode only supports readonly")
        child_permission_mode = permission_parts[1]
        delegate_args = permission_parts[2]
    target_parts = delegate_args.split(maxsplit=1)
    if len(target_parts) != 2:
        raise ValueError(_DELEGATE_START_USAGE)
    return AgentDelegateRequest(
        mode=mode,
        target_agent_id=target_parts[0],
        instruction=target_parts[1],
        child_permission_mode=child_permission_mode,
    )


def request_from_slash_args(args: str) -> AgentDelegateRequest:
    raw = str(args or "").strip()
    if not raw:
        raise ValueError(
            f"{_DELEGATE_START_USAGE} | "
            "/delegate list [limit] | "
            "/delegate status|result|resume|cancel <task-id> | "
            "/delegate review '<review-request-json>' | "
            "/delegate accept|reject '<child-artifact-json>'"
        )
    first, *remainder_parts = raw.split(maxsplit=1)
    remainder = remainder_parts[0] if remainder_parts else ""
    action = first.lower()
    if action == "list":
        if not remainder:
            return AgentDelegateRequest(mode="list")
        values = remainder.split()
        if len(values) != 1 or not values[0].isdigit():
            raise ValueError("Usage: /delegate list [limit]")
        limit = int(values[0])
        if not 1 <= limit <= 200:
            raise ValueError("/delegate list limit must be between 1 and 200")
        return AgentDelegateRequest(mode="list", limit=limit)
    if action in {"status", "result", "resume", "cancel"}:
        task_ids = remainder.split()
        if len(task_ids) != 1:
            raise ValueError(f"Usage: /delegate {action} <task-id>")
        return AgentDelegateRequest(mode=action, task_id=task_ids[0])
    if action in {"review", "accept", "reject"}:
        if not remainder:
            payload_name = (
                "review-request-json" if action == "review" else "child-artifact-json"
            )
            raise ValueError(f"Usage: /delegate {action} '<{payload_name}>'")
        payload_json = remainder
        if payload_json.startswith("'") and payload_json.endswith("'"):
            payload_json = payload_json[1:-1]
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"/delegate {action}: invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"/delegate {action}: JSON must be an object")
        if action != "review":
            return AgentDelegateRequest(mode=action, child_artifact=payload)
        reviewer_agent_id = payload.get("reviewer_agent_id")
        instruction = payload.get("instruction")
        child_artifact = payload.get("child_artifact")
        review_criteria = payload.get("review_criteria")
        if (
            not isinstance(reviewer_agent_id, str)
            or not reviewer_agent_id.strip()
            or not isinstance(instruction, str)
            or not instruction.strip()
            or not isinstance(child_artifact, dict)
            or not isinstance(review_criteria, list)
            or not review_criteria
            or any(
                not isinstance(item, str) or not item.strip()
                for item in review_criteria
            )
        ):
            raise ValueError(
                "/delegate review requires reviewer_agent_id, instruction, "
                "child_artifact, and non-empty review_criteria"
            )
        repository_instructions = payload.get("repository_instructions", "")
        if not isinstance(repository_instructions, str):
            raise ValueError(
                "/delegate review: repository_instructions must be a string"
            )
        return AgentDelegateRequest(
            mode="review",
            target_agent_id=reviewer_agent_id,
            instruction=instruction,
            child_artifact=child_artifact,
            review_criteria=tuple(review_criteria),
            repository_instructions=repository_instructions,
        )
    return _start_request_from_slash_args(
        raw=raw,
        action=action,
        remainder=remainder,
    )


def delegate_action_requires_task_id(action: str) -> bool:
    normalized = (action or "").strip().lower()
    return (
        normalized != "delegate"
        and normalized.removeprefix("delegate-") in _STATUS_MODES
    )


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
