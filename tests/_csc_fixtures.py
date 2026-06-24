"""Shared helpers for post-CSC test modules."""

from __future__ import annotations

from typing import Any

from openminion.base.config import AgentProfileConfig


def _csc_install_default_agent(config, **fields: Any) -> None:
    name = str(fields.get("name", "openminion") or "openminion")
    existing = (
        config.agents.get(name)
        if isinstance(getattr(config, "agents", None), dict)
        else None
    )
    if existing is None:
        existing = AgentProfileConfig(name=name)
    if "default_channel" not in fields and not getattr(existing, "default_channel", ""):
        existing.default_channel = "console"
    for _key, _value in fields.items():
        if _key == "name":
            existing.name = name
        else:
            setattr(existing, _key, _value)
    config.agents = {name: existing}
    if hasattr(config, "default_agent"):
        config.default_agent = name
