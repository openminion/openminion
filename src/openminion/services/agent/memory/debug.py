import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openminion.modules.memory.diagnostics.introspection import build_memory_snapshot
from openminion.modules.memory.storage.base import ListQueryOptions


def export_memory_debug(adapter: Any, output_dir: Path, *, session_id: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = output_dir / f"memory_debug_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    agent_scope = f"agent:{adapter._agent_id}"  # noqa: SLF001
    session_scope = f"session:{session_id}"

    def _to_dicts(records: list[Any]) -> list[dict]:
        result = []
        for record in records:
            try:
                import dataclasses

                result.append(dataclasses.asdict(record))
            except Exception:
                result.append({"repr": repr(record)})
        return result

    try:
        session_records = adapter._service.list(  # noqa: SLF001
            ListQueryOptions(scopes=[session_scope], limit=200)
        )
    except Exception:
        session_records = []

    try:
        agent_records = adapter._service.list(  # noqa: SLF001
            ListQueryOptions(scopes=[agent_scope], limit=200)
        )
    except Exception:
        agent_records = []

    try:
        global_records = adapter._service.list(  # noqa: SLF001
            ListQueryOptions(scopes=["global:system"], limit=200)
        )
    except Exception:
        global_records = []

    (out_dir / "session_records.json").write_text(
        json.dumps(_to_dicts(session_records), indent=2, default=str)
    )
    (out_dir / "agent_records.json").write_text(
        json.dumps(_to_dicts(agent_records), indent=2, default=str)
    )
    (out_dir / "global_records.json").write_text(
        json.dumps(_to_dicts(global_records), indent=2, default=str)
    )

    capsule, _ = adapter.build_context_with_metadata(  # noqa: SLF001
        session_id=session_id,
        user_message="",
    )
    (out_dir / "capsule_preview.md").write_text(capsule or "(empty)")

    retrieval, _ = adapter.build_retrieval_context_with_metadata(  # noqa: SLF001
        session_id=session_id,
        user_message="what do you know about me",
    )
    (out_dir / "retrieval_preview.md").write_text(retrieval or "(empty)")

    snap = build_memory_snapshot(
        adapter._service._store,  # noqa: SLF001
        session_id=session_id,
        agent_id=adapter._agent_id,  # noqa: SLF001
    )
    (out_dir / "snapshot.json").write_text(
        json.dumps(snap.model_dump(), indent=2, default=str)
    )

    (out_dir / "README.txt").write_text(
        f"Memory debug snapshot\n"
        f"agent_id:   {adapter._agent_id}\n"  # noqa: SLF001
        f"session_id: {session_id}\n"
        f"timestamp:  {ts}\n"
        f"files:\n"
        f"  session_records.json  - {len(session_records)} records\n"
        f"  agent_records.json    - {len(agent_records)} records\n"
        f"  global_records.json   - {len(global_records)} records\n"
        f"  capsule_preview.md    - what system prompt sees\n"
        f"  retrieval_preview.md  - dynamic retrieval output\n"
        f"  snapshot.json         - full introspection\n"
        f"  README.txt            - this file\n"
    )

    adapter._logger.info(  # noqa: SLF001
        "memory.debug_snapshot agent_id=%s session_id=%s output=%s",
        adapter._agent_id,  # noqa: SLF001
        session_id,
        out_dir,
    )
    return out_dir


__all__ = ["export_memory_debug"]
