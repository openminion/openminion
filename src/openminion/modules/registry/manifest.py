from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .errors import AgentRegError
from .models import AgentDescriptor


def load_manifest(path: str | Path) -> list[AgentDescriptor]:
    manifest_path = Path(path).expanduser().resolve(strict=False)
    if not manifest_path.exists():
        return []

    try:
        if manifest_path.suffix.lower() == ".json":
            raw: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive parse wrapper
        raise AgentRegError(
            "INVALID_MANIFEST", f"Failed to parse manifest {manifest_path}"
        ) from exc

    if raw is None:
        return []

    if isinstance(raw, list):
        return [AgentDescriptor.model_validate(item) for item in raw]

    if not isinstance(raw, dict):
        raise AgentRegError(
            "INVALID_MANIFEST", "Manifest root must be a mapping or list"
        )

    schema_version = raw.get("schema_version", 1)
    if int(schema_version) != 1:
        raise AgentRegError(
            "UNSUPPORTED_SCHEMA",
            f"Unsupported manifest schema_version={schema_version}; expected 1",
        )

    agents = raw.get("agents", [])
    if not isinstance(agents, list):
        raise AgentRegError(
            "INVALID_MANIFEST", "Manifest field 'agents' must be a list"
        )

    out: list[AgentDescriptor] = []
    for index, item in enumerate(agents):
        try:
            out.append(AgentDescriptor.model_validate(item))
        except (
            Exception
        ) as exc:  # pragma: no cover - pydantic details carry line context
            raise AgentRegError(
                "INVALID_MANIFEST",
                f"Invalid agent descriptor at index {index}",
            ) from exc
    return out
