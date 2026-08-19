from typing import Any

from openminion.modules.tool.errors import ToolRuntimeError

from .args import TaskWatchArgs
from .constants import WATCH_DEFAULT_ALLOWED_TOOLS


def watch_profile_tools(
    *,
    ctx: Any,
    validated: TaskWatchArgs,
) -> tuple[str, ...]:
    profile_id = str(validated.check_profile_id or "").strip()
    if not profile_id:
        return tuple(WATCH_DEFAULT_ALLOWED_TOOLS)

    registry = ctx.tool_registry
    exposure = getattr(registry, "exposure_service", None)
    profile = exposure.profile(profile_id) if exposure is not None else None
    if profile is None:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"Unknown watch check profile: {profile_id}",
            {
                "reason_code": "watch_profile_not_found",
                "check_profile_id": profile_id,
            },
        )
    if (
        profile.risk.tier != "read"
        or profile.risk.mutates_state
        or profile.risk.requires_approval
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "Watch check profiles must be read-only and approval-free",
            {
                "reason_code": "watch_profile_not_read_only",
                "check_profile_id": profile_id,
            },
        )

    target_id = str(validated.target_id or "").strip()
    if ctx.ops_service is None:
        raise ToolRuntimeError(
            "UNAVAILABLE",
            "Operations target registry is unavailable",
            {
                "reason_code": "watch_target_registry_unavailable",
                "target_id": target_id,
            },
        )
    try:
        ctx.ops_service.inspect_target(target_id)
    except KeyError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"Unknown operations target: {target_id}",
            {"reason_code": "watch_target_not_found", "target_id": target_id},
        ) from exc
    return tuple(sorted(profile.tool_names))
