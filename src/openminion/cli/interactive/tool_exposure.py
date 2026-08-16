"""Shared interactive command handling for tool exposure profiles."""

from __future__ import annotations

import shlex
from typing import Any


def tool_exposure_command(runtime: Any, text: str) -> str:
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        return f"Invalid /tools command: {exc}"
    action = parts[1].lower() if len(parts) > 1 else "status"
    if action == "list":
        return (
            "\n".join(
                f"{'✓' if enabled else '✗'}  {name}"
                for name, enabled in runtime.list_tools()
            )
            or "(none)"
        )
    if action == "status":
        profiles = runtime.tool_exposure_status().get("profiles", [])
        rows = [
            "  ".join(
                (
                    "active" if profile.get("active") else "hidden",
                    str(profile.get("profile_id", "")),
                    f"({profile.get('tier', '')})",
                )
            )
            for profile in profiles
        ]
        return "Tool exposure profiles:\n" + ("\n".join(rows) or "(none)")
    if action not in {"activate", "deactivate"} or len(parts) < 3:
        return (
            "Usage: /tools [status|list]\n"
            "       /tools activate <profile> [key=value ...]\n"
            "       /tools deactivate <profile> [target=<id>]"
        )
    profile_id = parts[2]
    try:
        options = dict(token.split("=", 1) for token in parts[3:])
    except ValueError:
        return "Tool profile options must use key=value syntax."
    target_id = options.get("target", "")
    if action == "deactivate":
        changed = runtime.deactivate_tool_profile(profile_id, target_id=target_id)
        return f"{'Deactivated' if changed else 'Not active'}: {profile_id}"
    approved = options.get("approved", "").lower() in {"1", "true", "yes"}
    try:
        activation = runtime.activate_tool_profile(
            profile_id,
            target_id=target_id,
            target_kind=options.get("target_kind", ""),
            credential_scopes=_option_tokens(options.get("credential", "")),
            dependencies=_option_tokens(options.get("dependency", "")),
            approved=approved,
            ttl_seconds=(float(options["ttl"]) if options.get("ttl") else None),
            activation_reason=options.get("reason", ""),
            approved_by=options.get("approved_by", ""),
            policy_source=options.get("policy_source", ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return f"Activation denied: {exc}"
    return f"Activated: {activation['profile_id']} ({activation['audit_id']})"


def _option_tokens(value: str) -> tuple[str, ...]:
    return tuple(token.strip() for token in value.split(",") if token.strip())


__all__ = ["tool_exposure_command"]
