from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config

_LOG = logging.getLogger(__name__)


def llm_debug_enabled(
    provider_name: str,
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> bool:
    env_owner = resolve_environment_config(env=env)
    if not env_owner.openminion_llm_debug:
        return False
    raw_filter = env_owner.openminion_llm_debug_provider
    if not raw_filter:
        return True
    providers = {item.strip() for item in raw_filter.split(",") if item.strip()}
    return str(provider_name or "").strip().lower() in providers


def llm_debug_dir(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> Path:
    env_owner = resolve_environment_config(env=env)
    configured = env_owner.openminion_llm_debug_dir.strip()
    if configured:
        return Path(configured).expanduser().resolve()
    data_root = env_owner.openminion_data_root.strip()
    if data_root:
        base = Path(data_root).expanduser()
        if not base.is_absolute():
            base = Path.cwd() / base
        return (base / "traces" / "llm").resolve()
    return (Path.cwd() / ".openminion" / "traces" / "llm").resolve()


def llm_debug_max_chars(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> int:
    env_owner = resolve_environment_config(env=env)
    configured = env_owner.openminion_llm_debug_max_chars
    if configured <= 0:
        return 4000
    return max(200, int(configured))


def truncate_debug_value(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        return value[:max_chars] + "...[truncated]"
    if isinstance(value, dict):
        return {k: truncate_debug_value(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [truncate_debug_value(item, max_chars) for item in value]
    return value


def write_llm_debug_event(
    event: dict[str, Any],
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> None:
    provider_name = str(event.get("provider") or "").strip()
    if not llm_debug_enabled(provider_name, env=env):
        return
    try:
        debug_dir = llm_debug_dir(env=env)
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d")
        filename = debug_dir / f"{provider_name or 'provider'}-{stamp}.jsonl"
        with filename.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True) + "\n")
    except Exception as exc:  # pragma: no cover - debug logging must never crash
        _LOG.debug("LLM debug logging failed: %s", exc)
