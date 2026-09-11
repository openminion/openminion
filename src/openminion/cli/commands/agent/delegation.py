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


@dataclass(frozen=True)
class AgentDelegateRequest:
    mode: str
    target_agent_id: str = ""
    instruction: str = ""
    task_id: str = ""
    timeout_seconds: int = 120
    child_artifact: dict[str, Any] | None = None
    workspace_root: str = ""
    review_criteria: tuple[str, ...] = ()
    repository_instructions: str = ""

    def tool_args(self) -> dict[str, Any]:
        return {
            "mode": normalize_delegate_mode(self.mode),
            "agent_id": self.target_agent_id.strip(),
            "instruction": self.instruction.strip(),
            "task_id": self.task_id.strip(),
            "timeout_seconds": self.timeout_seconds or 120,
            "child_artifact": dict(self.child_artifact or {}),
            "workspace_root": self.workspace_root.strip(),
            "review_criteria": list(self.review_criteria),
            "repository_instructions": self.repository_instructions.strip(),
        }


def normalize_delegate_mode(mode: str) -> str:
    normalized = (mode or "sync").strip().lower()
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
        "  /delegate review '<review-request-json>'\n"
        "  /delegate accept|reject '<child-artifact-json>'\n"
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
    artifactctl: Any | None = None,
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
    raw = str(args or "").strip()
    if not raw:
        raise ValueError(
            "Usage: /delegate <agent> <instruction...> | "
            "/delegate async <agent> <instruction...> | "
            "/delegate status|result|resume|cancel <task-id> | "
            "/delegate review '<review-request-json>' | "
            "/delegate accept|reject '<child-artifact-json>'"
        )
    first, *remainder_parts = raw.split(maxsplit=1)
    remainder = remainder_parts[0] if remainder_parts else ""
    action = first.lower()
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
    mode = action if action in {"sync", "async"} else "sync"
    if action in {"sync", "async"}:
        target_parts = remainder.split(maxsplit=1)
        if len(target_parts) != 2:
            raise ValueError("Usage: /delegate [sync|async] <agent> <instruction...>")
        target_agent_id, instruction = target_parts
    else:
        target_agent_id, instruction = first, remainder
    if not instruction:
        raise ValueError("Usage: /delegate [sync|async] <agent> <instruction...>")
    return AgentDelegateRequest(
        mode=mode,
        target_agent_id=target_agent_id,
        instruction=instruction,
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
