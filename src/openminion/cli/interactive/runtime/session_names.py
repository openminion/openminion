from typing import TYPE_CHECKING, Any


class SessionNameRuntimeMixin:
    """Session rename and resume-by-name helpers."""

    _rt: Any
    _target: str
    _working_dir: str | None

    if TYPE_CHECKING:

        @property
        def agent_id(self) -> str: ...

        def _ensure_agent_resolved(self) -> None: ...

    def rename_session(self, session_id: str, name: str) -> None:
        self._rt.sessions.update_session_metadata(
            session_id=session_id,
            patch={"name": name},
        )

    def resolve_session_by_name(self, name: str) -> str | None:
        target = str(name or "").strip()
        if not target:
            return None
        self._ensure_agent_resolved()
        if not self._working_dir:
            return None
        records = self._rt.sessions.list_sessions(
            agent_id=self.agent_id,
            target=self._target,
            metadata_filter={
                "name": target,
                "working_dir": self._working_dir,
            },
        )
        if records:
            return self._most_recent_session_id(records)
        folded = target.casefold()
        scoped = self._rt.sessions.list_sessions(
            agent_id=self.agent_id,
            target=self._target,
            newest_first=True,
            metadata_filter={"working_dir": self._working_dir},
        )
        matches = [
            record
            for record in scoped
            if str((getattr(record, "metadata", None) or {}).get("name", "") or "")
            .strip()
            .casefold()
            == folded
        ]
        return self._most_recent_session_id(matches)

    @staticmethod
    def _most_recent_session_id(records: list[Any]) -> str | None:
        best: Any | None = None
        best_key = ""
        for record in records or []:
            key = str(getattr(record, "updated_at", "") or "")
            if best is None or key > best_key:
                best = record
                best_key = key
        return str(best.id) if best is not None else None
