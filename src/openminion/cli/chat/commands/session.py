from __future__ import annotations

import shlex
from pathlib import Path

from openminion.cli.presentation import styles
from openminion.base.config import ActionPolicyConfig
from openminion.base.config.action_policy import (
    ACTION_POLICY_SESSION_OVERRIDE_KEY,
    normalize_action_policy_mode_override,
)
from openminion.cli.config import (
    resolve_cli_identity_db_path,
    resolve_cli_policy_db_path,
    resolve_identity_db_path as resolve_identity_db_config_path,
)
from openminion.cli.commands.session_policy import (
    read_session_action_policy_mode_override,
    render_action_policy_summary,
    resolve_configured_action_policy,
    resolve_effective_action_policy,
)
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.policy.runtime.action_policy import (
    policy_config_from_action_policy,
)

_POLICY_CATEGORY_TO_TOOL_PATTERNS: dict[str, tuple[tuple[str, str], ...]] = {
    "exec": (("exec", "*"),),
    "file": (
        ("file", "read"),
        ("file", "write"),
        ("file", "append"),
        ("file", "delete"),
        ("file", "copy"),
        ("file", "move"),
    ),
    "browser": (
        ("browser", "*"),
        ("browser.playwright", "*"),
        ("browser_pinchtab", "*"),
    ),
    "web": (
        ("fetch", "*"),
        ("gws", "*"),
        ("search", "*"),
        ("search.tavily", "*"),
        ("search.brave", "*"),
    ),
    "weather": (
        ("weather", "*"),
        ("weather.openmeteo", "*"),
    ),
    "ip": (
        ("ip", "*"),
        ("location", "*"),
    ),
}

_SKILL_SESSION_LOADED_KEY = "session_skill_loaded"
_SKILL_SESSION_UNLOADED_KEY = "session_skill_unloaded"
_SKILL_SELECTION_MODE_KEY = "skill_selection_mode"


def _normalize_skill_ids(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        skill_id = str(raw or "").strip()
        if not skill_id:
            continue
        lowered = skill_id.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(skill_id)
    return normalized


def _handle_agent_inspect(*, agent_id: str, config) -> None:
    from openminion.cli.commands.agents import agent_inspect
    from openminion.modules.storage.runtime.registry_store import (
        AgentRegistryStore,
    )

    storage_path = str(
        getattr(
            config.storage,
            "path",
            str(Path(".openminion") / "state" / "openminion.db"),
        )
    )
    registry = AgentRegistryStore(storage_path)
    agent_inspect(registry, agent_id, as_json=False)


def _handle_pair_status(*, config) -> None:

    pairing_enabled = getattr(
        getattr(config, "runtime", object()), "menu_pairing_enabled", True
    )

    if not pairing_enabled:
        print("Pairing is disabled via config (runtime.menu_pairing_enabled)")
        return

    try:
        from openminion.modules.controlplane.channels.telegram.state import (
            TelegramPollStateStore,
        )
        from openminion.modules.controlplane.channels.telegram.config import (
            load_config as load_telegram_config,
        )

        telegram_config_path = getattr(config, "telegram_config_path", None)
        if telegram_config_path:
            tg_cfg = load_telegram_config(telegram_config_path)
            store = TelegramPollStateStore(tg_cfg.polling.state_sqlite_path)
            pairings = store.list_pairings()

            if pairings:
                print("=== PAIRED CHANNELS ===")
                for p in pairings:
                    print(f"  User: {p.user_id}, Chat: {p.chat_id}, Scopes: {p.scopes}")
            else:
                print("No active pairings")
                print(
                    "To create a pairing, use: /pair create --user-id <id> --chat-id <id>"
                )
        else:
            print(
                "Telegram pairing available via CLI: openminion-controlplane-telegram pair-create --user-id <id> --chat-id <id>"
            )
    except ImportError:
        print("Telegram pairing not available")
        print(
            "To enable Telegram pairing, ensure openminion.modules.controlplane.channels.telegram is available"
        )
    except Exception as exc:
        print(f"Could not load pairing state: {exc}")
        print(
            "Telegram pairing available via CLI: openminion-controlplane-telegram pair-create --user-id <id> --chat-id <id>"
        )


def _handle_pair_create(*, line: str, config) -> None:

    pairing_enabled = getattr(
        getattr(config, "runtime", object()), "menu_pairing_enabled", True
    )

    if not pairing_enabled:
        print("Pairing is disabled via config (runtime.menu_pairing_enabled)")
        return

    user_id = None
    chat_id = None

    parts = line.split()
    i = 2
    while i < len(parts):
        if parts[i] == "--user-id" and i + 1 < len(parts):
            user_id = parts[i + 1]
            i += 2
        elif parts[i] == "--chat-id" and i + 1 < len(parts):
            chat_id = parts[i + 1]
            i += 2
        else:
            i += 1

    if not user_id and not chat_id:
        print(
            "Usage: /pair create --user-id <id> --chat-id <id> [--ttl-seconds <seconds>]"
        )
        print("Example: /pair create --user-id 123456789 --chat-id 123456789")
        return

    try:
        from openminion.cli.commands import channel

        output = channel.create_telegram_pair_token_from_chat_line(line=line, config=config)
    except Exception as exc:
        print(f"Could not create Telegram pairing token: {exc}")
        print(
            "Fallback: openminion-controlplane-telegram pair-create "
            "--user-id <id> --chat-id <id>"
        )
        return
    channel.print_pair_token_output(output)


def _handle_pair_revoke(*, line: str, config) -> None:

    pairing_enabled = getattr(
        getattr(config, "runtime", object()), "menu_pairing_enabled", True
    )

    if not pairing_enabled:
        print("Pairing is disabled via config (runtime.menu_pairing_enabled)")
        return

    parts = line.split()
    token_id = None
    if len(parts) >= 3 and parts[2] == "--token-id":
        token_id = parts[3] if len(parts) > 3 else None

    if not token_id:
        print("Usage: /pair revoke --token-id <token-id>")
        return

    print(f"Revoking token: {token_id}")
    print("Note: Token revocation is available via Telegram bot /revoke command")


def _build_policyctl(config):
    from openminion.modules.policy.runtime.service import PolicyCtl

    action_policy = getattr(config, "action_policy", None)
    db_path = resolve_cli_policy_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return (
        PolicyCtl.with_sqlite(
            db_path,
            config=policy_config_from_action_policy(
                action_policy or ActionPolicyConfig()
            ),
        ),
        str(db_path),
    )


def _open_brain_session_store(config) -> SQLiteSessionStore:
    storage_path = Path(
        getattr(
            getattr(config, "storage", object()),
            "path",
            str(Path(".openminion") / "state" / "openminion.db"),
        )
    )
    return SQLiteSessionStore(resolve_brain_sessions_db_path(storage_path=storage_path))


def _read_session_skill_state(
    store: SQLiteSessionStore,
    session_id: str,
) -> tuple[list[str], list[str], str | None]:
    latest = store.get_latest_working_state(session_id)
    if isinstance(latest, dict):
        state_inline = latest.get("state_inline")
        if isinstance(state_inline, dict):
            loaded = _normalize_skill_ids(state_inline.get(_SKILL_SESSION_LOADED_KEY))
            unloaded = _normalize_skill_ids(
                state_inline.get(_SKILL_SESSION_UNLOADED_KEY)
            )
            mode = (
                str(state_inline.get(_SKILL_SELECTION_MODE_KEY, "") or "")
                .strip()
                .lower()
                or None
            )
            if loaded or unloaded or mode is not None:
                return loaded, unloaded, mode
    session = store.get_session(session_id)
    if not isinstance(session, dict):
        return [], [], None
    meta = session.get("meta", {})
    if not isinstance(meta, dict):
        return [], [], None
    return (
        _normalize_skill_ids(meta.get(_SKILL_SESSION_LOADED_KEY)),
        _normalize_skill_ids(meta.get(_SKILL_SESSION_UNLOADED_KEY)),
        str(meta.get(_SKILL_SELECTION_MODE_KEY, "") or "").strip().lower() or None,
    )


def _write_session_action_policy_mode_override(
    store: SQLiteSessionStore,
    *,
    session_id: str,
    agent_id: str,
    mode: str,
) -> None:
    normalized = normalize_action_policy_mode_override(mode)
    if normalized is None:
        raise ValueError(f"invalid action policy mode: {mode}")
    session = store.get_session(session_id)
    if session is None:
        store.create_session(
            initial_agent_id=agent_id or None,
            session_id=session_id,
            meta={ACTION_POLICY_SESSION_OVERRIDE_KEY: normalized},
        )
    else:
        meta = dict(session.get("meta", {}) or {})
        meta[ACTION_POLICY_SESSION_OVERRIDE_KEY] = normalized
        store._session_helper.update_session(session_id, {"meta": meta})  # noqa: SLF001

    latest = store.get_latest_working_state(session_id)
    if isinstance(latest, dict) and isinstance(latest.get("state_inline"), dict):
        state_inline = dict(latest["state_inline"])
        state_inline[ACTION_POLICY_SESSION_OVERRIDE_KEY] = normalized
        store.put_working_state(session_id, state_inline=state_inline)


def _write_session_skill_state(
    store: SQLiteSessionStore,
    *,
    session_id: str,
    agent_id: str,
    loaded: list[str],
    unloaded: list[str],
    mode: str | None,
) -> None:
    normalized_loaded = _normalize_skill_ids(loaded)
    normalized_unloaded = _normalize_skill_ids(unloaded)
    normalized_mode = str(mode or "").strip().lower() or None
    session = store.get_session(session_id)
    if session is None:
        store.create_session(
            initial_agent_id=agent_id or None,
            session_id=session_id,
            meta={
                _SKILL_SESSION_LOADED_KEY: normalized_loaded,
                _SKILL_SESSION_UNLOADED_KEY: normalized_unloaded,
                _SKILL_SELECTION_MODE_KEY: normalized_mode,
            },
        )
    else:
        meta = dict(session.get("meta", {}) or {})
        meta[_SKILL_SESSION_LOADED_KEY] = normalized_loaded
        meta[_SKILL_SESSION_UNLOADED_KEY] = normalized_unloaded
        meta[_SKILL_SELECTION_MODE_KEY] = normalized_mode
        store._session_helper.update_session(session_id, {"meta": meta})  # noqa: SLF001

    latest = store.get_latest_working_state(session_id)
    if isinstance(latest, dict) and isinstance(latest.get("state_inline"), dict):
        state_inline = dict(latest["state_inline"])
        state_inline[_SKILL_SESSION_LOADED_KEY] = normalized_loaded
        state_inline[_SKILL_SESSION_UNLOADED_KEY] = normalized_unloaded
        state_inline[_SKILL_SELECTION_MODE_KEY] = normalized_mode
        store.put_working_state(session_id, state_inline=state_inline)


def _effective_action_policy_for_session(
    *,
    config,
    agent_id: str,
    session_id: str,
) -> tuple[ActionPolicyConfig, str]:
    configured_action_policy, config_source = resolve_configured_action_policy(
        config=config,
        agent_id=agent_id,
    )
    store = _open_brain_session_store(config)
    try:
        session_override = read_session_action_policy_mode_override(store, session_id)
    finally:
        store.close()
    return resolve_effective_action_policy(
        configured_action_policy,
        config_source=config_source,
        session_mode_override=session_override,
    )


def _handle_policy_command(
    *, line: str, config, agent_id: str, session_id: str
) -> None:
    try:
        parts = shlex.split(line)
    except ValueError as exc:
        print(
            styles.style(styles.StyleToken.ERROR, f"policy command parse error: {exc}")
        )
        return
    if len(parts) < 2 or parts[1] != "action":
        print(
            styles.style(
                styles.StyleToken.ERROR, "usage: /policy action [ask|auto|bypass]"
            )
        )
        return

    if len(parts) == 2:
        effective_policy, source = _effective_action_policy_for_session(
            config=config,
            agent_id=agent_id,
            session_id=session_id,
        )
        print(
            render_action_policy_summary(
                action_policy=effective_policy,
                source=source,
            )
        )
        return

    requested_mode = normalize_action_policy_mode_override(parts[2])
    if requested_mode is None:
        print(
            styles.style(
                styles.StyleToken.ERROR,
                "invalid action policy mode. expected ask, auto, or bypass",
            )
        )
        return

    store = _open_brain_session_store(config)
    try:
        _write_session_action_policy_mode_override(
            store,
            session_id=session_id,
            agent_id=agent_id,
            mode=requested_mode,
        )
    finally:
        store.close()

    effective_policy, source = _effective_action_policy_for_session(
        config=config,
        agent_id=agent_id,
        session_id=session_id,
    )
    print(
        styles.style(
            styles.StyleToken.SUCCESS, f"session action policy set to {requested_mode}"
        )
    )
    print(
        render_action_policy_summary(
            action_policy=effective_policy,
            source=source,
        )
    )


def _compile_category_patterns(category: str) -> list[tuple[str, str]]:
    normalized = str(category or "").strip().lower()
    if normalized == "all":
        merged: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for patterns in _POLICY_CATEGORY_TO_TOOL_PATTERNS.values():
            for pattern in patterns:
                if pattern in seen:
                    continue
                seen.add(pattern)
                merged.append(pattern)
        return merged
    patterns = _POLICY_CATEGORY_TO_TOOL_PATTERNS.get(normalized)
    if not patterns:
        raise ValueError(
            "unknown category. expected one of: "
            + ", ".join(
                sorted(list(_POLICY_CATEGORY_TO_TOOL_PATTERNS.keys()) + ["all"])
            )
        )
    return list(patterns)


def _grant_matches_patterns(grant, patterns: list[tuple[str, str]]) -> bool:
    tool = str(getattr(grant, "tool", "") or "")
    method = str(getattr(grant, "method", "") or "")
    for expected_tool, expected_method in patterns:
        if tool != expected_tool:
            continue
        if expected_method == "*" or method == expected_method:
            return True
    return False


def _handle_trust_command(*, line: str, config, session_id: str) -> None:
    from openminion.modules.policy.models import PolicyGrantInput

    try:
        parts = shlex.split(line)
    except ValueError as exc:
        print(
            styles.style(styles.StyleToken.ERROR, f"trust command parse error: {exc}")
        )
        print("Usage: /trust <category> [path_prefix]")
        return
    if len(parts) < 2:
        print(
            styles.style(
                styles.StyleToken.ERROR, "Usage: /trust <category> [path_prefix]"
            )
        )
        return

    category = str(parts[1] or "").strip().lower()
    path_prefix = str(parts[2] or "").strip() if len(parts) >= 3 else ""
    try:
        patterns = _compile_category_patterns(category)
    except ValueError as exc:
        print(styles.style(styles.StyleToken.ERROR, str(exc)))
        return

    target_json = {}
    if category == "file" and path_prefix:
        target_json["path_prefix"] = str(Path(path_prefix).expanduser().resolve())

    ctl, db_path = _build_policyctl(config)
    created = 0
    try:
        for tool, method in patterns:
            ctl.create_grant(
                PolicyGrantInput(
                    effect="allow",
                    tool=tool,
                    method=method,
                    target_json=dict(target_json),
                    duration_type="session",
                    subject_id="local",
                    session_id=session_id,
                    reason=f"chat:/trust:{category}",
                )
            )
            created += 1
    finally:
        close = getattr(ctl, "close", None)
        if callable(close):
            close()

    print(
        styles.style(
            styles.StyleToken.SUCCESS, f"trusted category={category} grants={created}"
        )
    )
    print(f"session_id={session_id} policy_db={db_path}")


def _handle_untrust_command(*, line: str, config, session_id: str) -> None:
    try:
        parts = shlex.split(line)
    except ValueError as exc:
        print(
            styles.style(styles.StyleToken.ERROR, f"untrust command parse error: {exc}")
        )
        print("Usage: /untrust <category>")
        return
    if len(parts) < 2:
        print(styles.style(styles.StyleToken.ERROR, "Usage: /untrust <category>"))
        return

    category = str(parts[1] or "").strip().lower()
    try:
        patterns = _compile_category_patterns(category)
    except ValueError as exc:
        print(styles.style(styles.StyleToken.ERROR, str(exc)))
        return

    ctl, db_path = _build_policyctl(config)
    revoked = 0
    try:
        active_grants = ctl.list_grants(
            subject_id="local", effect="allow", active_only=True
        )
        for grant in active_grants:
            if str(getattr(grant, "session_id", "") or "") != session_id:
                continue
            if not _grant_matches_patterns(grant, patterns):
                continue
            if ctl.revoke_grant(str(getattr(grant, "grant_id", "") or "")):
                revoked += 1
    finally:
        close = getattr(ctl, "close", None)
        if callable(close):
            close()

    print(
        styles.style(
            styles.StyleToken.SUCCESS,
            f"untrusted category={category} revoked={revoked}",
        )
    )
    print(f"session_id={session_id} policy_db={db_path}")


def _handle_grants_command(*, config, session_id: str) -> None:
    ctl, db_path = _build_policyctl(config)
    try:
        grants = [
            grant
            for grant in ctl.list_grants(
                subject_id="local", effect="allow", active_only=True
            )
            if str(getattr(grant, "session_id", "") or "") == session_id
        ]
    finally:
        close = getattr(ctl, "close", None)
        if callable(close):
            close()

    print(f"Active grants for session={session_id} policy_db={db_path}")
    if not grants:
        print("(none)")
        return
    for grant in grants:
        target_json = dict(getattr(grant, "target_json", {}) or {})
        target_suffix = f" target={target_json}" if target_json else ""
        print(
            f"- {grant.grant_id} tool={grant.tool} method={grant.method} "
            f"duration={grant.duration_type}{target_suffix}"
        )


def _resolve_identity_db_path(config) -> str:
    configured = str(resolve_identity_db_config_path(config) or "").strip()
    if configured:
        return configured
    return str(resolve_cli_identity_db_path(config))


def _build_identityctl(config):
    from openminion.modules.identity.runtime.service import IdentityCtl
    from openminion.modules.identity.storage.store import SQLiteIdentityStore

    db_path = Path(_resolve_identity_db_path(config)).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteIdentityStore(sqlite_path=str(db_path))
    return IdentityCtl(store=store), str(db_path)


def _print_identity_help(*, agent_id: str) -> None:
    print("Identity commands:")
    print("  /identity list")
    print(f"  /identity show [agent_id]           (default: {agent_id})")
    print(
        f"  /identity render [agent_id] [--purpose act] [--max-tokens 180]  (default: {agent_id})"
    )
    print("  /identity upsert <yaml_path>")
    print("  /identity delete <agent_id>")
    print("  /identity help")


def _handle_identity_command(
    *, line: str, config, agent_id: str, build_identityctl_fn=None
) -> None:
    if build_identityctl_fn is None:
        build_identityctl_fn = _build_identityctl

    try:
        parts = shlex.split(line)
    except ValueError as exc:
        print(
            styles.style(
                styles.StyleToken.ERROR, f"identity command parse error: {exc}"
            )
        )
        _print_identity_help(agent_id=agent_id)
        return

    if len(parts) < 2 or parts[1] in {"help", "-h", "--help"}:
        _print_identity_help(agent_id=agent_id)
        return

    try:
        ctl, db_path = build_identityctl_fn(config)
    except ModuleNotFoundError:
        print(
            styles.style(
                styles.StyleToken.ERROR, "openminion-identity module not installed"
            )
        )
        print("Install local modules and retry.")
        return
    except Exception as exc:
        print(
            styles.style(
                styles.StyleToken.ERROR, f"identity runtime init failed: {exc}"
            )
        )
        return

    subcmd = parts[1].strip().lower()
    try:
        if subcmd == "list":
            profiles = ctl.list_profiles()
            print(f"Identity DB: {db_path}")
            if not profiles:
                print("(no identity profiles found)")
                return
            print(f"{'Agent ID':<24} {'Display Name':<28} {'Version':<14}")
            print("-" * 70)
            for profile in profiles:
                version = str(getattr(profile, "profile_version", "") or "")[:12]
                print(
                    f"{profile.agent_id:<24} {profile.display_name:<28} {version:<14}"
                )
            return

        if subcmd == "show":
            target_agent = parts[2].strip() if len(parts) >= 3 else agent_id
            profile = ctl.get_profile(target_agent)
            if profile is None:
                print(
                    styles.style(
                        styles.StyleToken.WARNING, f"profile not found: {target_agent}"
                    )
                )
                return
            try:
                import yaml
            except ModuleNotFoundError:
                print(profile.model_dump(mode="json", exclude_none=True))
                return
            print(
                yaml.dump(
                    profile.model_dump(mode="python", exclude_none=True),
                    default_flow_style=False,
                    indent=2,
                )
            )
            return

        if subcmd == "render":
            target_agent = agent_id
            purpose = "act"
            max_tokens = 180
            idx = 2
            if idx < len(parts) and not parts[idx].startswith("--"):
                target_agent = parts[idx].strip()
                idx += 1
            while idx < len(parts):
                token = parts[idx]
                if token == "--purpose" and idx + 1 < len(parts):
                    purpose = parts[idx + 1].strip() or "act"
                    idx += 2
                    continue
                if token == "--max-tokens" and idx + 1 < len(parts):
                    max_tokens = max(1, int(parts[idx + 1]))
                    idx += 2
                    continue
                idx += 1

            snippet = ctl.render(
                agent_id=target_agent, purpose=purpose, max_tokens=max_tokens
            )
            print(snippet.text)
            print("")
            print(
                f"agent_id={target_agent} purpose={purpose} used_tokens={snippet.budget.used_tokens} max_tokens={snippet.budget.max_tokens}"
            )
            return

        if subcmd == "upsert":
            if len(parts) < 3:
                print(
                    styles.style(
                        styles.StyleToken.ERROR, "usage: /identity upsert <yaml_path>"
                    )
                )
                return
            yaml_path = Path(parts[2]).expanduser().resolve()
            if not yaml_path.exists():
                print(
                    styles.style(
                        styles.StyleToken.ERROR, f"file not found: {yaml_path}"
                    )
                )
                return
            loaded = ctl.load_profiles_from_path(yaml_path)
            if not loaded:
                print(styles.style(styles.StyleToken.WARNING, "no profiles loaded"))
                return
            for loaded_agent in loaded:
                print(
                    styles.style(styles.StyleToken.SUCCESS, f"loaded: {loaded_agent}")
                )
            return

        if subcmd == "delete":
            if len(parts) < 3:
                print(
                    styles.style(
                        styles.StyleToken.ERROR, "usage: /identity delete <agent_id>"
                    )
                )
                return
            target_agent = parts[2].strip()
            if not target_agent:
                print(
                    styles.style(
                        styles.StyleToken.ERROR, "usage: /identity delete <agent_id>"
                    )
                )
                return
            profile = ctl.get_profile(target_agent)
            if profile is None:
                print(
                    styles.style(
                        styles.StyleToken.WARNING, f"profile not found: {target_agent}"
                    )
                )
                return
            ctl.delete_profile(target_agent)
            print(styles.style(styles.StyleToken.SUCCESS, f"deleted: {target_agent}"))
            return

        print(
            styles.style(styles.StyleToken.ERROR, f"unknown identity command: {subcmd}")
        )
        _print_identity_help(agent_id=agent_id)
    except ValueError as exc:
        print(styles.style(styles.StyleToken.ERROR, f"identity command failed: {exc}"))
    except Exception as exc:
        print(
            styles.style(
                styles.StyleToken.ERROR,
                f"identity command failed: {type(exc).__name__}: {exc}",
            )
        )
    finally:
        close = getattr(ctl, "close", None)
        if callable(close):
            close()
