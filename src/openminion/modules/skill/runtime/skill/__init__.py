from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from openminion.modules.skill.config import SkillConfig, load_config
from openminion.modules.skill.constants import (
    DEFAULT_CONFIG_FILENAME,
    SKILL_TOOL_REGISTRY_AVAILABLE,
    SKILL_TOOL_REGISTRY_AVAILABLE_EMPTY,
)
from openminion.modules.skill.diagnostics.events import (
    emit_skill_counter,
    emit_skill_operation,
)
from openminion.modules.skill.runtime.skill.catalog import SkillCatalogMixin
from openminion.modules.skill.runtime.skill.ingest import SkillIngestMixin
from openminion.modules.skill.runtime.skill.matching import SkillMatchingMixin
from openminion.modules.skill.storage import SQLiteSkillStore
from openminion.modules.storage.engine import StorageEngine

from ...interfaces import SKILL_INTERFACE_VERSION

ArtifactIngestor = Callable[[str, str], str]
ArtifactLoader = Callable[[str], str | bytes]
SkillEventCallback = Callable[[str, dict[str, Any]], None]


class Skill(SkillIngestMixin, SkillCatalogMixin, SkillMatchingMixin):
    def __init__(
        self,
        config: str | Path | dict[str, Any] | SkillConfig = DEFAULT_CONFIG_FILENAME,
        *,
        home_root: Path | None = None,
        artifact_ingestor: ArtifactIngestor | None = None,
        artifact_loader: ArtifactLoader | None = None,
        known_tools: Iterable[str] | None = None,
        event_callback: SkillEventCallback | None = None,
        telemetryctl: Any | None = None,
        telemetry_session_id: str | None = None,
        telemetry_turn_id: str | None = None,
    ) -> None:
        self.config = load_config(config, home_root=home_root)
        known: set[str] = set(self.config.known_tools)
        known_tools_state = self.config.known_tools_state
        if known_tools is not None:
            known.update(str(item).strip() for item in known_tools if str(item).strip())
            known_tools_state = (
                SKILL_TOOL_REGISTRY_AVAILABLE
                if known
                else SKILL_TOOL_REGISTRY_AVAILABLE_EMPTY
            )

        self._known_tools = known
        self._known_tools_state = known_tools_state
        self._artifact_ingestor = artifact_ingestor
        self._artifact_loader = artifact_loader
        self._event_callback = event_callback
        self._telemetryctl = telemetryctl
        self._telemetry_session_id = str(telemetry_session_id or "").strip() or None
        self._telemetry_turn_id = str(telemetry_turn_id or "").strip() or None
        self._storage_engine = StorageEngine.from_paths(
            root_dir=self.config.blob_root,
            sqlite_path=self.config.sqlite_path,
            fallback_root=self.config.fallback_root,
            wal=self.config.wal,
            default_namespace="skill",
        )
        self._blob_store = self._storage_engine.blob_store
        self._record_store = self._storage_engine.record_store
        self._hybrid_store = self._storage_engine.module("skill")
        self.store = SQLiteSkillStore(record_store=self._record_store)

    @property
    def contract_version(self) -> str:
        return SKILL_INTERFACE_VERSION

    def close(self) -> None:
        self._storage_engine.close()

    def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        if self._event_callback is not None:
            try:
                self._event_callback(event_type, data)
            except Exception:
                pass

    def set_telemetry_context(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> None:
        self._telemetry_session_id = str(session_id or "").strip() or None
        self._telemetry_turn_id = str(turn_id or "").strip() or None

    def _telemetry_context_ids(self) -> tuple[str, str] | None:
        session_id = str(self._telemetry_session_id or "").strip()
        turn_id = str(self._telemetry_turn_id or "").strip()
        if not session_id or not turn_id:
            return None
        return session_id, turn_id

    def _emit_skill_operation(
        self,
        *,
        operation: str,
        status: str = "ok",
        extra: dict[str, Any] | None = None,
    ) -> None:
        telemetry_ids = self._telemetry_context_ids()
        if telemetry_ids is None:
            return
        session_id, turn_id = telemetry_ids
        emit_skill_operation(
            telemetryctl=self._telemetryctl,
            session_id=session_id,
            turn_id=turn_id,
            operation=operation,
            status=status,
            extra=extra,
        )

    def _emit_skill_counter(
        self,
        *,
        counter_name: str,
        value: float,
        status: str = "ok",
        extra: dict[str, Any] | None = None,
    ) -> None:
        telemetry_ids = self._telemetry_context_ids()
        if telemetry_ids is None:
            return
        session_id, turn_id = telemetry_ids
        emit_skill_counter(
            telemetryctl=self._telemetryctl,
            session_id=session_id,
            turn_id=turn_id,
            counter_name=counter_name,
            value=value,
            status=status,
            extra=extra,
        )
