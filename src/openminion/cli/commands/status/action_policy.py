from __future__ import annotations

from typing import Any

from openminion.base.config.core import resolve_default_agent_id
from openminion.cli.commands.session_policy import (
    read_session_action_policy_mode_override,
    resolve_configured_action_policy,
    resolve_effective_action_policy,
)
from openminion.cli.config import load_cli_manager, resolve_cli_policy_db_path
from openminion.cli.presentation.json_output import print_json_payload
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.policy.runtime.action_policy import (
    policy_config_from_action_policy,
)
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.storage.runtime.sqlite import resolve_database_path


def _format_grant_payload(grant) -> dict:
    return {
        "grant_id": str(grant.grant_id),
        "subject_id": str(grant.subject_id),
        "effect": str(grant.effect),
        "tool": str(grant.tool),
        "method": str(grant.method),
        "target": dict(grant.target_json or {}),
        "duration_type": str(grant.duration_type),
        "expires_at": grant.expires_at,
        "session_id": grant.session_id,
        "uses_count": int(grant.uses_count),
        "max_uses": grant.max_uses,
        "risk_floor": grant.risk_floor,
        "reason": grant.reason,
    }


def _resolve_action_policy_for_status(
    *, args, config, manager, agent_id: str | None, session_id: str
) -> tuple[Any, str, str, str | None]:
    storage_env = manager.env.snapshot()
    storage_env.setdefault("OPENMINION_HOME", str(manager.home_root))
    storage_env.setdefault("OPENMINION_DATA_ROOT", str(manager.data_root))
    configured_action_policy, config_source_level = resolve_configured_action_policy(
        config,
        agent_id=agent_id,
    )
    session_mode_override: str | None = None
    if session_id:
        resolved_storage_path = resolve_database_path(
            config.storage.path,
            env=storage_env,
        )
        store = SQLiteSessionStore(
            resolve_brain_sessions_db_path(
                storage_path=resolved_storage_path,
            )
        )
        try:
            session_mode_override = read_session_action_policy_mode_override(
                store,
                session_id,
            )
        finally:
            store.close()
    effective_action_policy, source_level = resolve_effective_action_policy(
        configured_action_policy,
        config_source=config_source_level,
        session_mode_override=session_mode_override,
    )
    return (
        effective_action_policy,
        source_level,
        config_source_level,
        session_mode_override,
    )


def _build_action_policy_effective_rules(
    *, effective_action_policy: Any, session_grants: list, config_source_level: str
) -> list[dict]:
    effective_rules: list[dict] = [
        {
            "rule": "default_action",
            "mode": str(effective_action_policy.default_action),
            "source": config_source_level,
        },
        {
            "rule": "allow_read_only_without_prompt",
            "mode": (
                "auto"
                if bool(effective_action_policy.allow_read_only_without_prompt)
                else "ask"
            ),
            "source": config_source_level,
        },
    ]
    for rule in list(getattr(effective_action_policy, "rules", []) or []):
        match = getattr(rule, "match", None)
        effective_rules.append(
            {
                "tool_category": str(getattr(match, "tool_category", "") or ""),
                "tool_name": str(getattr(match, "tool_name", "") or ""),
                "min_risk_class": str(getattr(match, "min_risk_class", "") or ""),
                "mode": str(getattr(rule, "mode", "ask") or "ask"),
                "source": config_source_level,
            }
        )
    for grant in session_grants:
        effective_rules.append(
            {
                "tool_category": str(grant.tool) if "." not in str(grant.tool) else "",
                "tool_name": (
                    str(grant.tool)
                    if str(grant.method) == "*"
                    else f"{grant.tool}.{grant.method}"
                ),
                "min_risk_class": str(grant.risk_floor or ""),
                "mode": "block" if str(grant.effect) == "deny" else "auto",
                "source": "session",
                "grant_id": str(grant.grant_id),
            }
        )
    return effective_rules


def _render_action_policy_status_text(
    *, payload: dict, session_id: str, session_active_grants: int
) -> None:
    print(
        "status action-policy: "
        f"permission_mode={payload['permission_mode']} "
        f"source={payload['source_level']} "
        f"policy_mode={payload['policy_mode']} "
        f"active_grants={len(payload['active_grants'])}"
    )
    print(
        "- source: "
        f"permission_mode={payload['source_attribution']['permission_mode']} "
        f"default_action={payload['source_attribution']['default_action']} "
        f"allow_read_only_without_prompt={payload['source_attribution']['allow_read_only_without_prompt']} "
        f"rules={payload['source_attribution']['rules']}"
    )
    if session_id:
        print(f"- session_id: {session_id}")
        print(f"- session_active_grants: {session_active_grants}")
    for rule in payload["effective_rules"]:
        print(
            "- rule: "
            f"tool_category={rule.get('tool_category', '*') or '*'} "
            f"tool_name={rule.get('tool_name', '*') or '*'} "
            f"mode={rule.get('mode', '')} source={rule.get('source', '')}"
        )


def run_action_policy_status(args, *, config) -> int:
    from openminion.modules.policy.runtime.service import PolicyCtl

    manager = load_cli_manager(args.config)
    data_root = manager.data_root
    agent_id = (
        str(getattr(args, "agent_id", "") or resolve_default_agent_id(config)).strip()
        or None
    )
    session_id = str(getattr(args, "session_id", "") or "").strip()
    (
        effective_action_policy,
        source_level,
        config_source_level,
        session_mode_override,
    ) = _resolve_action_policy_for_status(
        args=args,
        config=config,
        manager=manager,
        agent_id=agent_id,
        session_id=session_id,
    )

    policy_ctl = PolicyCtl.with_sqlite(
        resolve_cli_policy_db_path(
            home_root=manager.home_root,
            data_root=data_root,
        ),
        config=policy_config_from_action_policy(effective_action_policy),
    )
    try:
        active_grants = policy_ctl.list_grants(active_only=True)
        session_grants = (
            [
                grant
                for grant in active_grants
                if str(grant.session_id or "") == session_id
            ]
            if session_id
            else []
        )

        effective_rules = _build_action_policy_effective_rules(
            effective_action_policy=effective_action_policy,
            session_grants=session_grants,
            config_source_level=config_source_level,
        )

        payload = {
            "ok": True,
            "agent_id": agent_id or resolve_default_agent_id(config),
            "permission_mode": effective_action_policy.mode,
            "policy_mode": str(policy_ctl.mode()),
            "source_level": source_level,
            "resolved_action_policy": {
                "mode": effective_action_policy.mode,
                "default_action": effective_action_policy.default_action,
                "allow_read_only_without_prompt": bool(
                    effective_action_policy.allow_read_only_without_prompt
                ),
                "rule_count": len(list(effective_action_policy.rules or [])),
            },
            "source_attribution": {
                "permission_mode": source_level,
                "default_action": config_source_level,
                "allow_read_only_without_prompt": config_source_level,
                "rules": config_source_level,
            },
            "effective_rules": effective_rules,
            "active_grants": [_format_grant_payload(grant) for grant in active_grants],
        }
        if session_mode_override is not None:
            payload["session_action_policy_mode_override"] = session_mode_override
        if session_id:
            payload["session_id"] = session_id
            payload["session_active_grant_count"] = len(session_grants)

        if getattr(args, "json", False):
            print_json_payload(payload)
            return 0

        _render_action_policy_status_text(
            payload=payload,
            session_id=session_id,
            session_active_grants=len(session_grants),
        )
        return 0
    finally:
        policy_ctl.close()
