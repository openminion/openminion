from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def shorten_session_id(session_id: str, *, length: int = 8) -> str:
    value = str(session_id or "").strip()
    if not value:
        return ""
    if len(value) <= length:
        return value
    return value[:length]


_DEFAULT_MAX_DIR_LEN = 40


def shorten_working_dir(
    working_dir: str,
    *,
    home: str | None = None,
    max_length: int = _DEFAULT_MAX_DIR_LEN,
) -> str:
    value = str(working_dir or "").strip()
    if not value:
        return ""
    home_str = home if home is not None else os.path.expanduser("~")
    candidate = value
    try:
        resolved = Path(value).resolve(strict=False)
        home_path = Path(home_str).resolve(strict=False)
        if resolved == home_path:
            return "~"
        if str(resolved).startswith(str(home_path) + os.sep):
            candidate = "~/" + str(resolved.relative_to(home_path))
    except (OSError, RuntimeError, TypeError, ValueError):
        pass

    if max_length <= 0 or len(candidate) <= max_length:
        return candidate

    parts = candidate.split(os.sep)
    if len(parts) <= 3:
        return "…" + candidate[-(max_length - 1) :]
    head = parts[0]
    tail_parts: list[str] = []
    budget = max_length - len(head) - 4  # 4 = '/…/' separators + slash
    for part in reversed(parts[1:]):
        if not part:
            continue
        if len(part) + 1 > budget:
            break
        tail_parts.insert(0, part)
        budget -= len(part) + 1
    if not tail_parts:
        return "…" + candidate[-(max_length - 1) :]
    return f"{head}/…/" + "/".join(tail_parts)


def format_clock(now: datetime | None = None) -> str:
    dt = now if now is not None else datetime.now()
    return dt.strftime("%H:%M")


def format_runtime_label(runtime: Any) -> str:
    provider = str(getattr(runtime, "provider_name", "") or "").strip()
    model = str(getattr(runtime, "model_name", "") or "").strip()
    if provider and model:
        return f"{provider}/{model}"
    return model or provider or "—"


@dataclass
class RuntimeHeaderContext:
    agent_id: str = ""
    session_id: str = ""
    working_dir: str = ""
    provider: str = ""
    model: str = ""
    transport: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def short_session_id(self) -> str:
        return shorten_session_id(self.session_id)

    @property
    def short_working_dir(self) -> str:
        return shorten_working_dir(self.working_dir)

    def segment_labels(self) -> dict[str, str]:
        return {
            "agent_id": str(self.agent_id or "").strip(),
            "session_id": self.short_session_id,
            "working_dir": self.short_working_dir,
            "provider": str(self.provider or "").strip(),
            "model": str(self.model or "").strip(),
            "transport": str(self.transport or "").strip(),
        }


__all__ = [
    "RuntimeHeaderContext",
    "format_clock",
    "format_runtime_label",
    "shorten_session_id",
    "shorten_working_dir",
]
