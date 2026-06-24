from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from openminion.base.config.env import resolve_environment_config
from openminion.cli.config import resolve_cli_identity_db_path
from openminion.cli.presentation.json_output import print_json_payload
from openminion.cli.transport.daemon_client import daemon_request
from openminion.cli.identity.provenance import build_identity_provenance
from openminion.services.diagnostics.debug import (
    get_debug_registry,
    is_debug_surface_enabled,
)

from ..session import load_session_debug_snapshot


def _get_identity_debug_info(*, config, agent_id: str) -> dict[str, Any]:
    from openminion.modules.identity.runtime.service import IdentityCtl
    from openminion.modules.identity.storage.store import SQLiteIdentityStore

    identity_db_path = resolve_cli_identity_db_path(config)
    identity_db_path.parent.mkdir(parents=True, exist_ok=True)
    identityctl = IdentityCtl(
        store=SQLiteIdentityStore(sqlite_path=str(identity_db_path))
    )
    try:
        profile = identityctl.get_profile(agent_id)
        if profile is None:
            provenance = build_identity_provenance(None)
            return {
                "profile_present": False,
                "identity_db_path": str(identity_db_path),
                "profile_version": None,
                "render_version": None,
                "profile_revision": None,
                "bundle_imported": False,
                "bundle_fingerprint": "",
                **provenance,
                "errors": [f"identity profile not found: {agent_id}"],
                "warnings": [],
            }
        snippet = identityctl.render(
            agent_id=agent_id,
            purpose="act",
            max_tokens=200,
        )
        meta = dict(getattr(profile, "meta", {}) or {})
        provenance = build_identity_provenance(profile)
        return {
            "profile_present": True,
            "identity_db_path": str(identity_db_path),
            "profile_version": str(snippet.profile_version),
            "render_version": str(snippet.render_version),
            "profile_revision": int(profile.profile_revision),
            "bundle_imported": bool(meta.get("bundle_imported")),
            "bundle_fingerprint": str(meta.get("bundle_fingerprint") or ""),
            **provenance,
            "errors": [],
            "warnings": [],
        }
    except Exception as exc:
        provenance = build_identity_provenance(None)
        return {
            "profile_present": False,
            "identity_db_path": str(identity_db_path),
            "profile_version": None,
            "render_version": None,
            "profile_revision": None,
            "bundle_imported": False,
            "bundle_fingerprint": "",
            **provenance,
            "errors": [str(exc)],
            "warnings": [],
        }
    finally:
        try:
            identityctl.close()
        except Exception:
            pass


def _get_telemetry_debug_info(*, config, session_id: str) -> dict[str, Any]:

    telemetry_enabled = getattr(
        getattr(config, "runtime", object()), "telemetry_enabled", False
    )
    if not telemetry_enabled:
        return {
            "enabled": False,
            "reason": "telemetry not enabled in config",
        }

    try:
        from openminion.modules.telemetry.adapter import create_telemetry_adapter

        db_path = (
            getattr(getattr(config, "runtime", object()), "telemetry_db_path", "")
            or None
        )
        telemetryctl = create_telemetry_adapter(db_path=db_path)
        import asyncio

        summary = asyncio.run(telemetryctl._service.get_session_summary(session_id))
        cost = asyncio.run(telemetryctl._service.get_session_cost(session_id))
        return {
            "enabled": True,
            "session_id": session_id,
            "tick_count": summary.tick_count,
            "tool_call_count": summary.tool_call_count,
            "llm_call_count": summary.llm_call_count,
            "total_input_tokens": summary.total_input_tokens,
            "total_output_tokens": summary.total_output_tokens,
            "total_cached_tokens": summary.total_cached_tokens,
            "elapsed_ms": summary.elapsed_ms,
            "estimated_cost_usd": cost.estimated_cost_usd,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "error": str(exc),
        }


def _get_reactions_debug_info(*, config) -> dict[str, Any]:

    reactions_enabled = getattr(
        getattr(config, "runtime", object()), "reactions_enabled", True
    )
    reactions_policy = getattr(
        getattr(config, "runtime", object()), "reactions_default_policy", "allow"
    )

    try:
        importlib.import_module("openminion.tools.reaction.plugin")
        has_plugin = True
    except ImportError:
        has_plugin = False

    return {
        "enabled": reactions_enabled,
        "default_policy": reactions_policy,
        "plugin_installed": has_plugin,
        "available": has_plugin and reactions_enabled,
    }


def _get_search_provider_info(*, env: dict[str, str] | None = None) -> dict[str, Any]:
    env_config = resolve_environment_config()
    tavily_configured = bool(env_config.get("TAVILY_API_KEY", "").strip())
    brave_configured = bool(env_config.get("BRAVE_API_KEY", "").strip())

    if env:
        if not tavily_configured:
            tavily_configured = bool(str(env.get("TAVILY_API_KEY", "")).strip())
        if not brave_configured:
            brave_configured = bool(str(env.get("BRAVE_API_KEY", "")).strip())

    available = []
    if tavily_configured:
        available.append("tavily")
    if brave_configured:
        available.append("brave")

    return {
        "tavily_configured": tavily_configured,
        "brave_configured": brave_configured,
        "available_providers": available,
    }


def _resolve_search_provider(
    configured_provider: str, provider_info: dict[str, Any]
) -> str:
    provider = str(configured_provider or "auto").strip().lower()
    if provider not in {"auto", "tavily", "brave"}:
        provider = "auto"

    available = set(provider_info.get("available_providers") or [])
    if provider == "auto":
        if "tavily" in available:
            return "tavily"
        if "brave" in available:
            return "brave"
        return "tavily"
    return provider


def _get_search_provider_debug_info(*, config) -> dict[str, Any]:
    config_env = getattr(getattr(config, "runtime", object()), "env", {})
    provider_info = _get_search_provider_info(env=config_env)
    resolved = _resolve_search_provider("auto", provider_info)

    return {
        "resolved_provider": resolved,
        "tavily_configured": provider_info.get("tavily_configured", False),
        "brave_configured": provider_info.get("brave_configured", False),
        "available_providers": provider_info.get("available_providers", []),
    }


def _get_rlm_debug_info(*, config) -> dict[str, Any]:

    rlm_config = getattr(config, "rlm", None)
    rlm_enabled = getattr(rlm_config, "enabled", True) if rlm_config else True

    return {
        "rlm_active": rlm_enabled,
        "adapter_type": "RLMAdapter" if rlm_enabled else "LocalRLMAdapter",
        "config": {
            "enabled": rlm_enabled,
            "allow_empty_augmentation": getattr(
                rlm_config, "allow_empty_augmentation", True
            ),
            "quality_good_threshold": getattr(
                rlm_config, "quality_good_threshold", 0.6
            ),
            "quality_ok_threshold": getattr(rlm_config, "quality_ok_threshold", 0.35),
        }
        if rlm_config
        else {"enabled": True},
    }


def _get_introspection_debug_info(*, config, session_id: str) -> dict[str, Any]:

    memory_info = {
        "available": False,
        "snapshot_timestamp": None,
        "total_records": 0,
        "degraded": False,
        "degraded_reason": None,
    }

    retrieval_info = {
        "available": False,
        "snapshot_timestamp": None,
        "last_strategy": "none",
        "last_hit_count": 0,
    }

    try:
        from openminion.modules.memory.diagnostics.introspection import (
            build_memory_snapshot,
        )

        memory_snapshot = build_memory_snapshot(
            store=None,
            session_id=session_id,
            agent_id="debug",
        )
        memory_info = {
            "available": memory_snapshot.memory_available,
            "snapshot_timestamp": memory_snapshot.snapshot_timestamp,
            "total_records": memory_snapshot.total_records,
            "session_records": memory_snapshot.session_records,
            "agent_records": memory_snapshot.agent_records,
            "global_records": memory_snapshot.global_records,
            "candidate_count": memory_snapshot.candidate_count,
            "vector_search_available": memory_snapshot.vector_search_available,
            "degraded": memory_snapshot.degraded,
            "degraded_reason": memory_snapshot.degraded_reason,
            "recent_highlights_count": len(memory_snapshot.recent_highlights),
        }
    except Exception as exc:
        memory_info["error"] = str(exc)

    return {
        "memory": memory_info,
        "retrieval": retrieval_info,
        "introspection_intent_supported": True,
        "note": "Introspection data shows runtime memory/retrieval snapshot status",
    }


def _get_module_usage_debug_info(*, config, session_id: str) -> dict[str, Any]:

    storage_path = str(
        getattr(getattr(config, "storage", object()), "path", "") or ""
    ).strip()

    modules_info: dict[str, dict[str, Any]] = {
        "openminion-memory": {
            "importable": False,
            "available": False,
            "last_used_at": None,
            "last_success_at": None,
            "recent_calls": 0,
            "degraded_reason": None,
        },
        "openminion-retrieve": {
            "importable": False,
            "available": False,
            "last_used_at": None,
            "last_success_at": None,
            "recent_calls": 0,
            "degraded_reason": None,
        },
        "openminion-identity": {
            "importable": False,
            "available": False,
            "last_used_at": None,
            "last_success_at": None,
            "recent_calls": 0,
            "degraded_reason": None,
        },
    }

    try:
        importlib.import_module("openminion.modules.memory")
        modules_info["openminion-memory"]["importable"] = True
    except ImportError:
        modules_info["openminion-memory"]["degraded_reason"] = "module_not_installed"

    try:
        importlib.import_module("openminion.modules.retrieve")
        modules_info["openminion-retrieve"]["importable"] = True
    except ImportError:
        modules_info["openminion-retrieve"]["degraded_reason"] = "module_not_installed"

    try:
        importlib.import_module("openminion.modules.identity")
        modules_info["openminion-identity"]["importable"] = True
    except ImportError:
        modules_info["openminion-identity"]["degraded_reason"] = "module_not_installed"

    if storage_path:
        try:
            db_path = Path(storage_path).expanduser()
            if db_path.exists():
                from openminion.modules.storage.record_store import RecordStoreSQLite

                store = RecordStoreSQLite(db_path, wal=True)
                conn = store.connection
                try:
                    for module_name in modules_info:
                        rows = conn.execute(
                            """
                            SELECT
                                COUNT(*) as call_count,
                                MAX(timestamp) as last_used,
                                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as success_count
                            FROM session_events
                            WHERE session_id = ?
                                AND event_type LIKE '%.completed'
                                AND payload_json LIKE ?
                            """,
                            (session_id, f"%{module_name}%"),
                        ).fetchall()

                        if rows:
                            row = rows[0]
                            if row["call_count"]:
                                modules_info[module_name]["recent_calls"] = int(
                                    row["call_count"]
                                )
                            if row["last_used"]:
                                modules_info[module_name]["last_used_at"] = str(
                                    row["last_used"]
                                )

                        if modules_info[module_name]["importable"]:
                            modules_info[module_name]["available"] = True
                            if modules_info[module_name]["recent_calls"] == 0:
                                modules_info[module_name]["degraded_reason"] = (
                                    "not_yet_used_in_session"
                                )
                        else:
                            modules_info[module_name]["available"] = False
                finally:
                    store.close()
        except Exception as exc:
            for module_name in modules_info:
                if not modules_info[module_name]["degraded_reason"]:
                    modules_info[module_name]["degraded_reason"] = (
                        f"storage_query_failed: {str(exc)[:50]}"
                    )
    else:
        for module_name in modules_info:
            if not modules_info[module_name]["degraded_reason"]:
                modules_info[module_name]["degraded_reason"] = "storage_not_configured"

    return {
        "modules": modules_info,
        "note": "CTED-06: Usage metrics distinguish installed vs actively used",
    }


def _print_debug_context(
    *,
    config,
    agent_id: str,
    session_id: str,
    transport: str,
    last_turn_debug: dict[str, Any],
) -> None:
    storage_path = str(
        getattr(getattr(config, "storage", object()), "path", "") or ""
    ).strip()

    identity_info = _get_identity_debug_info(config=config, agent_id=agent_id)
    telemetry_info = _get_telemetry_debug_info(config=config, session_id=session_id)
    reactions_info = _get_reactions_debug_info(config=config)
    search_provider_info = _get_search_provider_debug_info(config=config)
    rlm_info = _get_rlm_debug_info(config=config)
    introspection_info = _get_introspection_debug_info(
        config=config, session_id=session_id
    )
    module_usage_info = _get_module_usage_debug_info(
        config=config, session_id=session_id
    )

    payload = {
        "agent": agent_id,
        "session": session_id,
        "transport": transport,
        "identity": identity_info,
        "telemetry": telemetry_info,
        "reactions": reactions_info,
        "search_provider": search_provider_info,
        "rlm": rlm_info,
        "introspection": introspection_info,
        "module_usage": module_usage_info,
        "last_turn": dict(last_turn_debug or {}),
        "session_debug": load_session_debug_snapshot(
            storage_path=storage_path,
            session_id=session_id,
        ),
    }
    print_json_payload(payload, default=str)


def _handle_debug_command(
    *,
    line: str,
    config,
    agent_id: str,
    session_id: str,
    transport: str,
    last_turn_debug: dict[str, Any],
    endpoint,
    print_debug_context_fn=None,
    print_module_debug_fn=None,
) -> None:
    if print_debug_context_fn is None:
        print_debug_context_fn = _print_debug_context
    if print_module_debug_fn is None:
        print_module_debug_fn = _print_module_debug

    if not is_debug_surface_enabled(config, surface="chat"):
        print(
            "[chat] /debug is disabled by config (runtime.debug_enabled/runtime.debug_chat_enabled)."
        )
        return
    parts = line.split()
    module_filter = None
    if len(parts) > 1 and parts[1].startswith("--module="):
        module_filter = parts[1].split("=", 1)[1].strip()

    if module_filter:
        print_module_debug_fn(module_name=module_filter, endpoint=endpoint)
    else:
        print_debug_context_fn(
            config=config,
            agent_id=agent_id,
            session_id=session_id,
            transport=transport,
            last_turn_debug=last_turn_debug,
        )


def _print_module_debug(*, module_name: str, endpoint) -> None:
    if endpoint is not None:
        try:
            status, payload = daemon_request(
                endpoint=endpoint,
                method="GET",
                path=f"/v1/debug/modules/{module_name}",
                timeout_s=5,
            )
            if status < 400 and payload.get("ok"):
                print_json_payload(payload.get("module"), default=str)
                return
        except RuntimeError:
            pass

    registry = get_debug_registry()
    from openminion.cli.commands.debug import _register_core_providers

    _register_core_providers(registry)

    provider = registry.get_module(module_name)
    if provider is None:
        print(f"Error: Unknown module '{module_name}'")
        return
    try:
        print_json_payload(provider.get_debug().to_dict(), default=str)
    except Exception as exc:
        print(f"Error: Failed to get debug info: {exc}")
