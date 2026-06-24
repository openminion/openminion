from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

from openminion.cli.presentation import styles
from openminion.base.config import resolve_agent_config, skill_value_to_list
from openminion.cli.config import resolve_cli_roots
from openminion.modules.brain.bootstrap.skill.pipeline import describe_skill_catalog
from openminion.tools.skill import url_ingest as _skill_url_ingest
from .session import (
    _open_brain_session_store,
    _read_session_skill_state,
    _write_session_skill_state,
)

_extract_skill_name_from_url = _skill_url_ingest.extract_skill_name_from_url
_fetch_skill_from_url = _skill_url_ingest.fetch_skill_markdown_from_url
_ingest_skill_url = _skill_url_ingest.ingest_skill_url
_is_blocked_skill_host = _skill_url_ingest.is_blocked_skill_host
_is_valid_markdown_content = _skill_url_ingest.is_valid_markdown_content


def _build_skill_from_config(config) -> Any:
    """Build a Skill instance that shares the running agent's storage path."""
    from openminion.modules.skill import Skill

    skill_cfg: object = {}
    try:
        from openminion.modules.skill.config import (
            from_base_config as skill_from_base_config,
        )

        roots = resolve_cli_roots()
        home_root = roots.home_root
        data_root = roots.data_root

        base = config if config is not None else object()
        skill_cfg = skill_from_base_config(
            base_config=base,
            home_root=home_root,
            data_root=data_root,
        )
    except (ImportError, AttributeError, TypeError, ValueError):
        pass

    return Skill(config=skill_cfg)


def _allowed_skill_catalog(*, config, agent_id: str) -> list[dict[str, str]]:
    ctl = _build_skill_from_config(config)
    try:
        catalog = list(ctl.catalog_summaries(agent_id=agent_id) or [])
    finally:
        ctl.close()
    resolved_agent = resolve_agent_config(config, agent_id or None)
    allowed_ids = {
        str(item).strip().lower()
        for item in list(getattr(resolved_agent, "skill_catalog", []) or [])
        if str(item).strip()
    }
    if not allowed_ids:
        return catalog
    return [
        item
        for item in catalog
        if str(item.get("id", "") or "").strip().lower() in allowed_ids
    ]


def _skill_session_summary(
    *,
    config,
    agent_id: str,
    session_id: str,
) -> tuple[dict[str, str], list[dict[str, str]], Any, list[str], list[str], str | None]:
    catalog = _allowed_skill_catalog(config=config, agent_id=agent_id)
    store = _open_brain_session_store(config)
    try:
        loaded, unloaded, mode = _read_session_skill_state(store, session_id)
    finally:
        store.close()
    state = SimpleNamespace(
        session_skill_loaded=list(loaded),
        session_skill_unloaded=list(unloaded),
        skill_selection_mode=mode,
    )
    resolved_agent = resolve_agent_config(config, agent_id or None)
    catalog_state = describe_skill_catalog(
        profile=resolved_agent,
        state=state,
        catalog=catalog,
    )
    configured_auto, configured_skills = skill_value_to_list(
        getattr(resolved_agent, "skill", None)
    )
    summary = {
        "configured_auto": "yes" if configured_auto else "no",
        "configured_skills": ", ".join(configured_skills) if configured_skills else "",
        "session_mode": mode or "manual",
        "selection_mode": catalog_state.projected_selection_mode,
        "effective_count": str(len(catalog_state.effective_catalog)),
        "capacity": str(catalog_state.capacity),
    }
    return summary, catalog, catalog_state, loaded, unloaded, mode


def _print_skill_list(*, config, agent_id: str, session_id: str) -> None:
    summary, _catalog, catalog_state, loaded, unloaded, _mode = _skill_session_summary(
        config=config,
        agent_id=agent_id,
        session_id=session_id,
    )
    print(
        "skills: "
        f"selection_mode={summary['selection_mode']} "
        f"effective={summary['effective_count']} "
        f"capacity={summary['capacity']} "
        f"session_mode={summary['session_mode']}"
    )
    if summary["configured_skills"]:
        print(f"configured_skills: {summary['configured_skills']}")
    if loaded:
        print(f"session_loaded: {', '.join(loaded)}")
    if unloaded:
        print(f"session_unloaded: {', '.join(unloaded)}")
    if not catalog_state.effective_catalog:
        print("effective_skills: (none)")
        return
    print("effective_skills:")
    for entry in catalog_state.effective_catalog:
        skill_id = str(entry.get("id", "") or "").strip()
        source = catalog_state.sources.get(skill_id, "config")
        print(f"  - {skill_id} source={source}")


def _set_skill_session_state(
    *,
    config,
    agent_id: str,
    session_id: str,
    loaded: list[str],
    unloaded: list[str],
    mode: str | None,
) -> None:
    store = _open_brain_session_store(config)
    try:
        _write_session_skill_state(
            store,
            session_id=session_id,
            agent_id=agent_id,
            loaded=loaded,
            unloaded=unloaded,
            mode=mode,
        )
    finally:
        store.close()


def _handle_skill_command(
    *,
    line: str,
    config,
    agent_id: str | None = None,
    session_id: str | None = None,
) -> None:
    parts = line.split()
    if len(parts) < 2:
        print("Usage:")
        print("  /skill ingest <path>    Ingest a SKILL.md file from local path")
        print("  /skill ingest <url>     Ingest a SKILL.md file from URL")
        print("  /skill catalog          List ingested skills")
        print("  /skill list             Show effective session skills")
        print("  /skill load <id>        Load a catalog skill for this session")
        print("  /skill unload <id>      Unload a skill for this session")
        print("  /skill auto             Use auto-selection for this session")
        print("  /skill clear            Clear session skill overrides")
        print("  /skill remove <id>      Remove an ingested skill")
        print("  /skill help             Show this help")
        return

    subcmd = parts[1]
    if subcmd == "help":
        print("Skill commands:")
        print("  /skill ingest <path>    Ingest a SKILL.md file from local path")
        print("  /skill ingest <url>     Ingest a SKILL.md file from URL (http/https)")
        print("  /skill catalog          List ingested skills")
        print("  /skill list             Show effective session skills")
        print("  /skill load <id>        Load a catalog skill for this session")
        print("  /skill unload <id>      Unload a skill for this session")
        print("  /skill auto             Use auto-selection for this session")
        print("  /skill clear            Clear session skill overrides")
        print("  /skill remove <id>      Remove an ingested skill")
        print("  /skill help             Show this help")
        return

    if subcmd == "catalog":
        _run_chat_skill_list(config)
        return

    if subcmd == "list":
        if not agent_id or not session_id:
            print("session skill commands require an active agent and session")
            return
        _print_skill_list(config=config, agent_id=agent_id, session_id=session_id)
        return

    if subcmd == "load":
        if not agent_id or not session_id:
            print("session skill commands require an active agent and session")
            return
        if len(parts) < 3:
            print("Usage: /skill load <id>")
            return
        skill_id = str(parts[2] or "").strip()
        summary, catalog, catalog_state, loaded, unloaded, _mode = (
            _skill_session_summary(
                config=config,
                agent_id=agent_id,
                session_id=session_id,
            )
        )
        del summary
        catalog_ids = {
            str(item.get("id", "") or "").strip()
            for item in catalog
            if str(item.get("id", "") or "").strip()
        }
        if skill_id not in catalog_ids:
            print(
                styles.style(
                    styles.StyleToken.ERROR,
                    f"skill not available in this agent catalog: {skill_id}",
                )
            )
            return
        if skill_id.lower() not in {item.lower() for item in loaded}:
            effective_count = len(
                list(getattr(catalog_state, "effective_catalog", []) or [])
            )
            capacity = int(getattr(catalog_state, "capacity", 1) or 1)
            if effective_count >= capacity:
                print(
                    styles.style(
                        styles.StyleToken.ERROR,
                        "skill capacity reached; unload a skill first via /skill unload <id>",
                    )
                )
                return
        unloaded = [item for item in unloaded if item.lower() != skill_id.lower()]
        if skill_id.lower() not in {item.lower() for item in loaded}:
            loaded.append(skill_id)
        _set_skill_session_state(
            config=config,
            agent_id=agent_id,
            session_id=session_id,
            loaded=loaded,
            unloaded=unloaded,
            mode=None,
        )
        print(styles.style(styles.StyleToken.SUCCESS, f"loaded skill {skill_id}"))
        _print_skill_list(config=config, agent_id=agent_id, session_id=session_id)
        return

    if subcmd == "unload":
        if not agent_id or not session_id:
            print("session skill commands require an active agent and session")
            return
        if len(parts) < 3:
            print("Usage: /skill unload <id>")
            return
        skill_id = str(parts[2] or "").strip()
        _summary, catalog, _catalog_state, loaded, unloaded, _mode = (
            _skill_session_summary(
                config=config,
                agent_id=agent_id,
                session_id=session_id,
            )
        )
        catalog_ids = {
            str(item.get("id", "") or "").strip()
            for item in catalog
            if str(item.get("id", "") or "").strip()
        }
        loaded = [item for item in loaded if item.lower() != skill_id.lower()]
        if skill_id in catalog_ids and skill_id.lower() not in {
            item.lower() for item in unloaded
        }:
            unloaded.append(skill_id)
        _set_skill_session_state(
            config=config,
            agent_id=agent_id,
            session_id=session_id,
            loaded=loaded,
            unloaded=unloaded,
            mode=None,
        )
        print(styles.style(styles.StyleToken.SUCCESS, f"unloaded skill {skill_id}"))
        _print_skill_list(config=config, agent_id=agent_id, session_id=session_id)
        return

    if subcmd == "auto":
        if not agent_id or not session_id:
            print("session skill commands require an active agent and session")
            return
        _summary, _catalog, _catalog_state, loaded, unloaded, _mode = (
            _skill_session_summary(
                config=config,
                agent_id=agent_id,
                session_id=session_id,
            )
        )
        _set_skill_session_state(
            config=config,
            agent_id=agent_id,
            session_id=session_id,
            loaded=loaded,
            unloaded=unloaded,
            mode="auto",
        )
        print(styles.style(styles.StyleToken.SUCCESS, "session skill mode set to auto"))
        _print_skill_list(config=config, agent_id=agent_id, session_id=session_id)
        return

    if subcmd == "clear":
        if not agent_id or not session_id:
            print("session skill commands require an active agent and session")
            return
        _set_skill_session_state(
            config=config,
            agent_id=agent_id,
            session_id=session_id,
            loaded=[],
            unloaded=[],
            mode=None,
        )
        print(
            styles.style(styles.StyleToken.SUCCESS, "cleared session skill overrides")
        )
        _print_skill_list(config=config, agent_id=agent_id, session_id=session_id)
        return

    if subcmd == "remove":
        if len(parts) < 3:
            print("Usage:")
            print("  /skill remove <id>      Remove a skill by id")
            print("  /skill remove <id> --version <hash>  Remove a specific version")
            return
        skill_id = parts[2].strip()
        version = None
        if "--version" in parts:
            idx = parts.index("--version")
            if idx + 1 < len(parts):
                version = parts[idx + 1].strip() or None
        _run_chat_skill_remove(skill_id=skill_id, version_hash=version, config=config)
        return

    if subcmd == "ingest":
        if len(parts) < 3:
            print("Usage:")
            print("  /skill ingest <path>    Ingest from local path")
            print(
                "  /skill ingest <url>     Ingest from URL (e.g., https://example.com/SKILL.md)"
            )
            print("\nExamples:")
            print(
                "  /skill ingest openminion/examples/skills/plan-checkpoints/SKILL.md"
            )
            print(
                "  /skill ingest https://raw.githubusercontent.com/org/repo/main/SKILL.md"
            )
            return
        skill_source = parts[2]

        if skill_source.startswith(("http://", "https://")):
            _run_chat_skill_ingest_url(skill_source, config)
        else:
            _run_chat_skill_ingest(skill_source, config)
        return

    print(f"Unknown skill command: {subcmd}")
    print("Use /skill help for available commands")


def _run_chat_skill_ingest(path: str, config) -> None:
    """Execute skill ingest from chat context (local path)."""
    try:
        from openminion.modules.skill.errors import SkillError
    except ModuleNotFoundError:
        print("Error: openminion-skill module not available")
        print("Install with: pip install openminion-skill")
        return

    try:
        ctl = _build_skill_from_config(config)
        try:
            skill_id, version_hash, warnings = ctl.ingest_file(path=path)
            print(
                styles.style(styles.StyleToken.SUCCESS, "Successfully ingested skill")
            )
            print(f"  skill_id: {skill_id}")
            print(f"  version_hash: {version_hash[:16]}...")
            if warnings:
                print(styles.style(styles.StyleToken.WARNING, "Warnings:"))
                for w in warnings[:5]:
                    print(f"    - {w}")
        finally:
            ctl.close()
    except SkillError as exc:
        print(styles.style(styles.StyleToken.ERROR, f"Error: {exc.code}"))
        print(f"  {exc.message}")
        if exc.details:
            for k, v in exc.details.items():
                print(f"  {k}: {v}")
    except FileNotFoundError:
        print(styles.style(styles.StyleToken.ERROR, "Error: File not found"))
        print(f"  Path: {path}")
    except Exception as exc:
        print(styles.style(styles.StyleToken.ERROR, f"Error: {type(exc).__name__}"))
        print(f"  {str(exc)}")


def _run_chat_skill_remove(*, skill_id: str, version_hash: str | None, config) -> None:
    """Execute skill removal from chat context."""
    try:
        from openminion.modules.skill.errors import SkillError
    except ModuleNotFoundError:
        print("Error: openminion-skill module not available")
        print("Install with: pip install openminion-skill")
        return

    try:
        ctl = _build_skill_from_config(config)
        try:
            counts = ctl.delete_skill(skill_id=skill_id, version_hash=version_hash)
            print(styles.style(styles.StyleToken.SUCCESS, "Successfully removed skill"))
            print(f"  skill_id: {skill_id}")
            if version_hash:
                print(f"  version_hash: {version_hash[:16]}...")
            print(
                f"  deleted: skills={counts.get('skills', 0)} versions={counts.get('versions', 0)}"
            )
        finally:
            ctl.close()
    except SkillError as exc:
        print(styles.style(styles.StyleToken.ERROR, f"Error: {exc.code}"))
        print(f"  {exc.message}")
        if exc.details:
            for k, v in exc.details.items():
                print(f"  {k}: {v}")
    except Exception as exc:
        print(styles.style(styles.StyleToken.ERROR, f"Error: {type(exc).__name__}"))
        print(f"  {str(exc)}")


def _run_chat_skill_ingest_url(url: str, config) -> None:
    """SNUM-03: Execute skill ingest from chat context (remote URL)."""
    try:
        from openminion.modules.skill.errors import SkillError
    except ModuleNotFoundError:
        print("Error: openminion-skill module not available")
        print("Install with: pip install openminion-skill")
        return

    try:
        ctl = _build_skill_from_config(config)
        try:
            result = _ingest_skill_url(ctl, url=url)
        finally:
            ctl.close()
    except SkillError as exc:
        print(styles.style(styles.StyleToken.ERROR, f"Ingest error: {exc.code}"))
        print(f"  {exc.message}")
        if exc.details:
            for k, v in exc.details.items():
                print(f"  {k}: {v}")
    except Exception as exc:
        print(styles.style(styles.StyleToken.ERROR, f"Error: {type(exc).__name__}"))
        print(f"  {str(exc)}")
    if not result["ok"]:
        error = result.get("error") or {}
        print(
            styles.style(
                styles.StyleToken.ERROR,
                f"URL ingest failed: {error.get('code', 'UNKNOWN_ERROR')}",
            )
        )
        print(f"  {error.get('message', 'Unknown error')}")
        print(f"  URL: {url}")
        return

    print(
        styles.style(styles.StyleToken.SUCCESS, "Successfully ingested skill from URL")
    )
    print(f"  skill_id: {result['skill_id']}")
    print(f"  version_hash: {str(result['version_hash'])[:16]}...")
    print("  source_type: url")
    print(f"  source_url: {url}")
    print(f"  content_length: {result.get('content_length', 'unknown')}")
    print(f"  content_type: {result.get('content_type', 'unknown')}")
    if result.get("truncated"):
        print(
            styles.style(
                styles.StyleToken.WARNING, "  Content was truncated to size limit"
            )
        )
    if result.get("warnings"):
        print(styles.style(styles.StyleToken.WARNING, "Warnings:"))
        for warning in list(result["warnings"])[:5]:
            print(f"    - {warning}")


def _run_chat_skill_list(config) -> None:
    """Execute skill list from chat context."""
    try:
        from openminion.modules.skill.errors import SkillError
    except ModuleNotFoundError:
        print("Error: openminion-skill module not available")
        print("Install with: pip install openminion-skill")
        return

    try:
        ctl = _build_skill_from_config(config)
        try:
            skills = ctl.list_skills({})
            if not skills:
                print("No skills found.")
                return
            print(f"Found {len(skills)} skill(s):")
            for s in skills:
                print(
                    f"  {s['skill_id']} | {s.get('name', 'N/A')} | {s.get('status', 'N/A')}"
                )
        finally:
            ctl.close()
    except SkillError as exc:
        print(styles.style(styles.StyleToken.ERROR, f"Error: {exc.code}"))
        print(f"  {exc.message}")
    except Exception as exc:
        print(styles.style(styles.StyleToken.ERROR, f"Error: {type(exc).__name__}"))
        print(f"  {str(exc)}")


def _extract_skill_source(message: str) -> dict[str, str] | None:
    """SNUM-01: Extract skill source (path or URL) from NL message."""
    url_patterns = [
        r"(https?://[^\s]+\.md(?:\?[^\s]*)?)",
        r"(https?://[^\s]+/[^\s]*\.md(?:\?[^\s]*)?)",
    ]
    for pattern in url_patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return {"type": "url", "value": match.group(1)}

    path_patterns = [
        r"(/[^\s]+\.md)",
        r"([A-Za-z]:\\[^\s]+\.md)",
        r"(\.\.?/[^\s]+\.md)",
    ]
    for pattern in path_patterns:
        match = re.search(pattern, message)
        if match:
            return {"type": "path", "value": match.group(1)}

    return None
