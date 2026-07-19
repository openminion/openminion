from __future__ import annotations

import argparse
import time
from typing import Any

from openminion.cli.config import load_cli_config, resolve_cli_roots
from openminion.cli.presentation.json_output import print_json_payload
from openminion.cli.transport.daemon_client import (
    daemon_is_reachable,
    resolve_daemon_endpoint,
)
from openminion.cli.parser.flags import add_json_output_flag
from openminion.base.config import resolve_default_agent_id
from openminion.modules.storage.runtime.registry_store import AgentRegistryStore
from openminion.cli.commands.daemon import daemon_logs


def _default_agent_id(config) -> str:
    try:
        return resolve_default_agent_id(config)
    except Exception:
        return "openminion"


def run_agent_operator(args) -> int:
    action = str(getattr(args, "agent_command", "")).strip().lower()

    config_path = args.config
    config = load_cli_config(config_path)
    storage_path = str(config.storage.path)
    registry = AgentRegistryStore(storage_path)

    if action == "ls":
        return agent_ls(registry, as_json=getattr(args, "json", False))
    if action == "status":
        agent_id = getattr(args, "agent_id", None) or _default_agent_id(config)
        return agent_status(registry, agent_id, as_json=getattr(args, "json", False))
    if action == "spawn":
        agent_id = getattr(args, "agent_id", None) or _default_agent_id(config)
        return agent_spawn(registry, agent_id)
    if action == "stop":
        agent_id = getattr(args, "agent_id", None) or _default_agent_id(config)
        return agent_stop(registry, agent_id)
    if action == "restart":
        agent_id = getattr(args, "agent_id", None) or _default_agent_id(config)
        _ = agent_stop(registry, agent_id)
        time.sleep(1)
        return agent_spawn(registry, agent_id)
    if action == "logs":
        lines = int(getattr(args, "lines", 200) or 200)
        return daemon_logs(config_path, lines=lines)
    if action == "attach":
        agent_id = getattr(args, "agent_id", None) or _default_agent_id(config)
        return agent_attach(registry, agent_id, config_path)
    if action == "inspect":
        agent_id = getattr(args, "agent_id", None) or _default_agent_id(config)
        return agent_inspect(registry, agent_id, as_json=getattr(args, "json", False))

    raise RuntimeError("Unknown agent operator command")


def agent_ls(registry: AgentRegistryStore, *, as_json: bool) -> int:
    agents = registry.list_agents()
    heartbeats = {
        hb.agent_id: hb
        for hb in registry.list_heartbeats()
        if not registry.is_agent_stale(hb.agent_id)
    }

    results = []
    for a in agents:
        hb = heartbeats.get(a.agent_id)
        state = hb.status if hb else "stopped"
        results.append(
            {
                "agent_id": a.agent_id,
                "display_name": a.display_name,
                "status": state,
                "pid": hb.pid if hb else 0,
                "host": hb.host if hb else "",
                "port": hb.port if hb else 0,
            }
        )

    if as_json:
        print_json_payload(results)
    else:
        print(
            f"{'AGENT ID':<20} | {'DISPLAY NAME':<20} | {'STATUS':<15} | {'PID':<10} | {'ADDRESS'}"
        )
        print("-" * 80)
        for r in results:
            addr = f"{r['host']}:{r['port']}" if r["host"] and r["port"] else "-"
            print(
                f"{r['agent_id']:<20} | {r['display_name']:<20} | {r['status']:<15} | {r['pid']:<10} | {addr}"
            )

    return 0


def agent_status(registry: AgentRegistryStore, agent_id: str, *, as_json: bool) -> int:
    agent = registry.get_agent(agent_id)
    hb = registry.get_heartbeat(agent_id)
    is_stale = registry.is_agent_stale(agent_id)

    status = hb.status if hb and not is_stale else "stopped"

    payload = {
        "agent_id": agent_id,
        "registered": agent is not None,
        "display_name": agent.display_name if agent else "",
        "status": status,
        "heartbeat": None,
    }

    if hb and not is_stale:
        payload["heartbeat"] = {
            "pid": hb.pid,
            "host": hb.host,
            "port": hb.port,
            "active_run_id": hb.active_run_id,
            "started_at": hb.started_at,
            "last_heartbeat_at": hb.last_heartbeat_at,
        }

    if as_json:
        print_json_payload(payload)
    else:
        print(f"Agent ID: {payload['agent_id']}")
        print(f"Status:   {payload['status']}")
        if payload["heartbeat"]:
            print(f"PID:      {payload['heartbeat']['pid']}")
            print(
                f"Address:  {payload['heartbeat']['host']}:{payload['heartbeat']['port']}"
            )
            print(f"Run ID:   {payload['heartbeat']['active_run_id']}")
            print(f"Uptime:   Since {payload['heartbeat']['started_at']}")

    return 0


def agent_spawn(registry: AgentRegistryStore, agent_id: str) -> int:
    agent = registry.get_agent(agent_id)
    if not agent:
        registry.upsert_agent(agent_id=agent_id, display_name=agent_id)

    registry.set_agent_status(agent_id=agent_id, status="starting")
    print(f"Agent '{agent_id}' spawn signal queued (status set to starting).")
    print("In Topology A, agents are spawned by the main daemon loop.")
    return 0


def agent_stop(registry: AgentRegistryStore, agent_id: str) -> int:
    agent = registry.get_agent(agent_id)
    if not agent:
        print(f"Agent '{agent_id}' not found in registry.")
        return 1

    registry.set_agent_status(agent_id=agent_id, status="stopping")
    print(f"Agent '{agent_id}' stop signal queued (status set to stopping).")
    return 0


def agent_attach(
    registry: AgentRegistryStore, agent_id: str, config_path: str | None
) -> int:
    agent = registry.get_agent(agent_id)
    if not agent:
        print(f"Agent '{agent_id}' not found.")
        return 1

    hb = registry.get_heartbeat(agent_id)
    is_stale = registry.is_agent_stale(agent_id)

    if not hb or is_stale:
        print(f"Agent '{agent_id}' is not running (no active heartbeat).")
        return 1

    endpoint = resolve_daemon_endpoint(config_path)
    if not daemon_is_reachable(endpoint):
        print(f"Daemon at {endpoint.host}:{endpoint.port} is unreachable.")
        return 1

    print(f"Attaching to agent '{agent_id}' (daemon pid={hb.pid})...")
    print("Press Ctrl+C to detach.")

    try:
        while True:
            time.sleep(1.0)
            hb = registry.get_heartbeat(agent_id)
            if not hb or registry.is_agent_stale(agent_id):
                print(f"\nAgent '{agent_id}' disconnected (heartbeat lost).")
                break
    except KeyboardInterrupt:
        print("\nDetached.")

    return 0


def _get_pairing_state() -> dict[str, Any]:
    pairing_data = {
        "available": False,
        "paired_channels": [],
        "pending_tokens": 0,
        "last_pairing_event": None,
    }

    try:
        from openminion.modules.controlplane.channels.telegram.state import (
            TelegramPollStateStore,
        )
        import os

        roots = resolve_cli_roots()
        home_root = roots.home_root
        data_root = roots.data_root
        possible_paths = [
            str((data_root / "controlplane" / "telegram-poll-state.db").resolve()),
            str((home_root / ".openminion" / "telegram" / "state.db").resolve()),
            os.path.expanduser("~/.openminion/telegram/state.db"),
        ]

        for db_path in possible_paths:
            if os.path.exists(db_path):
                store = TelegramPollStateStore(db_path)
                pairings = store.list_pairings()
                pairing_data["available"] = True
                pairing_data["paired_channels"] = [
                    {"user_id": p.user_id, "chat_id": p.chat_id, "scopes": p.scopes}
                    for p in pairings
                ]
                break
    except ImportError:
        pairing_data["available"] = False
    except Exception:
        pairing_data["available"] = False

    return pairing_data


def _get_provider_diagnostics(config) -> dict[str, Any]:
    from urllib.parse import urlparse

    diagnostics = {
        "provider": "echo",
        "model": "",
        "base_url": "",
        "base_url_sanitized": "",
        "tool_call_strategy": "hybrid",
        "last_error": None,
    }

    try:
        if config is None:
            try:
                config = load_cli_config(None)
            except Exception:
                diagnostics["last_error"] = "could not load config"
                return diagnostics

        default_agent_id = _default_agent_id(config)
        agent_profile = getattr(config, "agents", {}).get(default_agent_id)
        agent_provider = getattr(agent_profile, "provider", None) or "echo"
        diagnostics["provider"] = agent_provider.strip().lower() or "echo"

        provider_config = getattr(
            config.providers, agent_provider.strip().lower(), None
        )
        if provider_config:
            model = getattr(provider_config, "model", "")
            diagnostics["model"] = str(model).strip() if model else ""

            base_url = getattr(provider_config, "base_url", "")
            if base_url:
                diagnostics["base_url"] = str(base_url).strip()
                parsed = urlparse(str(base_url).strip())
                if parsed.netloc:
                    diagnostics["base_url_sanitized"] = (
                        f"{parsed.scheme}://{parsed.netloc}"
                    )
                else:
                    diagnostics["base_url_sanitized"] = str(base_url).strip()
            else:
                if diagnostics["provider"] == "ollama":
                    diagnostics["base_url_sanitized"] = "http://127.0.0.1:11434"
                elif diagnostics["provider"] == "openrouter":
                    diagnostics["base_url_sanitized"] = "https://openrouter.ai"

            tool_strategy = getattr(provider_config, "tool_call_strategy", "")
            diagnostics["tool_call_strategy"] = str(tool_strategy).strip() or "hybrid"

        from openminion.modules.llm.providers.bridge import llmctl_bridge_available

        diagnostics["bridge_available"] = llmctl_bridge_available()

    except Exception as e:
        diagnostics["last_error"] = str(e)

    return diagnostics


def _collect_skill_inspect_data() -> dict[str, Any]:
    skills_data: dict[str, Any] = {
        "loaded": [],
        "source_roots": [],
        "index_stats": {"total": 0, "by_tag": {}},
        "last_skill_event": None,
    }
    try:
        from openminion.modules.skill.runtime.skill import SkillIndex
        import os

        roots = resolve_cli_roots()
        data_root = roots.data_root
        idx_candidates = [
            str((data_root / "skill" / "index.json").resolve()),
            os.path.expanduser("~/.openminion/skills/index.json"),
        ]
        for idx_path in idx_candidates:
            if not os.path.exists(idx_path):
                continue
            idx = SkillIndex.load(idx_path)
            skills_data["index_stats"]["total"] = len(idx.list_skills())
            skills_data["source_roots"] = [idx_path]
            for sid in idx.list_skills():
                skill = idx.get(sid)
                if skill:
                    for tag in skill.metadata.get("tags", []):
                        if tag not in skills_data["index_stats"]["by_tag"]:
                            skills_data["index_stats"]["by_tag"][tag] = 0
                        skills_data["index_stats"]["by_tag"][tag] += 1
            break
    except Exception:
        pass
    return skills_data


def _collect_provider_inspect_data() -> dict[str, Any]:
    try:
        return _get_provider_diagnostics(None)
    except Exception:
        return {
            "provider": "unknown",
            "model": "",
            "base_url_sanitized": "-",
            "tool_call_strategy": "-",
            "bridge_available": False,
            "last_error": "config unavailable",
        }


def _collect_tools_inspect_data() -> dict[str, Any]:
    tools_data: dict[str, Any] = {"catalog_summary": {"total": 0, "by_category": {}}}
    runtime = None
    try:
        from openminion.api.runtime import APIRuntime

        runtime = APIRuntime.from_config_path(None)
        tool_specs = runtime.tools.provider_specs()
        tools_data["catalog_summary"]["total"] = len(tool_specs)
        by_category: dict[str, int] = {}
        for spec in tool_specs:
            cat = getattr(spec, "category", "unknown") or "unknown"
            by_category[cat] = by_category.get(cat, 0) + 1
        tools_data["catalog_summary"]["by_category"] = by_category
    except Exception:
        pass
    finally:
        if runtime:
            runtime.close()
    return tools_data


def _build_agent_inspect_payload(
    *,
    agent_id: str,
    agent,
    hb,
    is_stale: bool,
    status: str,
    skills_data: dict[str, Any],
    provider_info: dict[str, Any],
    tools_data: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent_id": agent_id,
        "registered": agent is not None,
        "display_name": agent.display_name if agent else "",
        "status": status,
        "runtime": {
            "lane": "daemon" if hb and not is_stale else "in-process",
            "provider": provider_info.get("provider", "openminion"),
            "fallback_reason": None,
        },
        "provider_diagnostics": provider_info,
        "skills": skills_data,
        "tools": tools_data,
        "identity": {
            "state": "active" if status == "running" else "inactive",
        },
        "pairing": _get_pairing_state(),
        "health": {
            "flags": ["healthy"] if status == "running" else ["stopped"],
        },
    }
    if hb and not is_stale:
        payload["runtime"]["heartbeat"] = {
            "pid": hb.pid,
            "host": hb.host,
            "port": hb.port,
            "active_run_id": hb.active_run_id,
            "started_at": hb.started_at,
            "last_heartbeat_at": hb.last_heartbeat_at,
        }
    return payload


def _render_agent_inspect_text(payload: dict[str, Any]) -> None:
    print(f"Agent:        {payload['agent_id']}")
    print(f"Display Name: {payload['display_name']}")
    print(f"Status:       {payload['status']}")
    print("Runtime:")
    print(f"  Lane:       {payload['runtime']['lane']}")
    print(f"  Provider:   {payload['runtime']['provider']}")
    pd = payload.get("provider_diagnostics", {})
    print("Provider:")
    print(f"  Model:      {pd.get('model', '-')}")
    print(f"  Base URL:   {pd.get('base_url_sanitized', '-')}")
    print(f"  Strategy:   {pd.get('tool_call_strategy', '-')}")
    print(
        f"  Bridge:     {'available' if pd.get('bridge_available') else 'unavailable'}"
    )
    if pd.get("last_error"):
        print(f"  Error:      {pd['last_error']}")
    print("Skills:")
    print(f"  Loaded:     {len(payload['skills']['loaded'])} skills")
    print(f"  Index:      {payload['skills']['index_stats']['total']} total")
    print("Tools:")
    print(f"  Catalog:    {payload['tools']['catalog_summary']['total']} total")
    print("Identity:")
    print(f"  State:      {payload['identity']['state']}")
    pairing = payload.get("pairing", {})
    print("Pairing:")
    print(f"  Available: {pairing.get('available', False)}")
    if pairing.get("paired_channels"):
        print(f"  Channels:   {len(pairing['paired_channels'])} paired")
    else:
        print("  Channels:   0 paired")
    print("Health:")
    for flag in payload["health"]["flags"]:
        print(f"  - {flag}")


def agent_inspect(registry: AgentRegistryStore, agent_id: str, *, as_json: bool) -> int:
    agent = registry.get_agent(agent_id)
    hb = registry.get_heartbeat(agent_id)
    is_stale = registry.is_agent_stale(agent_id)
    status = hb.status if hb and not is_stale else "stopped"

    skills_data = _collect_skill_inspect_data()
    provider_info = _collect_provider_inspect_data()
    tools_data = _collect_tools_inspect_data()
    payload = _build_agent_inspect_payload(
        agent_id=agent_id,
        agent=agent,
        hb=hb,
        is_stale=is_stale,
        status=status,
        skills_data=skills_data,
        provider_info=provider_info,
        tools_data=tools_data,
    )

    if as_json:
        print_json_payload(payload)
    else:
        _render_agent_inspect_text(payload)
    return 0


def add_agent_operator_subcommands(agent_parser: argparse.ArgumentParser) -> None:
    agent_subcommands = agent_parser.add_subparsers(dest="agent_command")

    agent_ls = agent_subcommands.add_parser("ls", help="List registered agents")
    add_json_output_flag(agent_ls)
    agent_ls.set_defaults(handler=run_agent_operator, needs_app=False)

    agent_status_cmd = agent_subcommands.add_parser(
        "status", help="Get agent status and heartbeat"
    )
    agent_status_cmd.add_argument("--agent-id", default=None, help="Agent id to query")
    add_json_output_flag(agent_status_cmd)
    agent_status_cmd.set_defaults(handler=run_agent_operator, needs_app=False)

    agent_spawn = agent_subcommands.add_parser(
        "spawn", help="Queue spawn signal for agent"
    )
    agent_spawn.add_argument("--agent-id", default=None, help="Agent id to spawn")
    agent_spawn.set_defaults(handler=run_agent_operator, needs_app=False)

    agent_stop = agent_subcommands.add_parser(
        "stop", help="Queue stop signal for agent"
    )
    agent_stop.add_argument("--agent-id", default=None, help="Agent id to stop")
    agent_stop.set_defaults(handler=run_agent_operator, needs_app=False)

    agent_restart = agent_subcommands.add_parser("restart", help="Restart an agent")
    agent_restart.add_argument("--agent-id", default=None, help="Agent id to restart")
    agent_restart.set_defaults(handler=run_agent_operator, needs_app=False)

    agent_logs = agent_subcommands.add_parser("logs", help="Tail agent daemon logs")
    agent_logs.add_argument("--lines", type=int, default=200, help="Line count")
    agent_logs.set_defaults(handler=run_agent_operator, needs_app=False)

    agent_attach = agent_subcommands.add_parser(
        "attach", help="Proxy live log/event output from daemon"
    )
    agent_attach.add_argument("--agent-id", default=None, help="Agent id to attach")
    agent_attach.set_defaults(handler=run_agent_operator, needs_app=False)

    agent_inspect_cmd = agent_subcommands.add_parser(
        "inspect", help="Inspect agent runtime state, skills, and health"
    )
    agent_inspect_cmd.add_argument(
        "--agent-id", default=None, help="Agent id to inspect"
    )
    add_json_output_flag(agent_inspect_cmd)
    agent_inspect_cmd.set_defaults(handler=run_agent_operator, needs_app=False)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    agent_ctl = subparsers.add_parser(
        "agent-ctl",
        help=argparse.SUPPRESS,
    )
    add_agent_operator_subcommands(agent_ctl)
