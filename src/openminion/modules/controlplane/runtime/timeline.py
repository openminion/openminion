from typing import Any


class SessionTimelineMirror:
    """Mirror controlplane audit events into the session timeline."""

    def __init__(self, session_store: Any | None = None) -> None:
        self._store = session_store

    def set_store(self, store: Any) -> None:
        self._store = store

    def mirror_event(
        self,
        event_type: str,
        session_id: str | None,
        agent_id: str | None,
        trace_id: str,
        details: dict[str, Any],
        outcome: str = "ok",
    ) -> bool:
        if self._store is None:
            return False

        if not session_id:
            return False

        try:
            event_summary = f"[{event_type}] {outcome}"
            if details:
                detail_str = ", ".join(f"{k}={v}" for k, v in list(details.items())[:3])
                event_summary += f": {detail_str}"

            if hasattr(self._store, "append_turn"):
                self._store.append_turn(
                    session_id=session_id,
                    role="system",
                    content=f"[AUDIT] {event_summary}",
                    meta={
                        "event_type": event_type,
                        "trace_id": trace_id,
                        "agent_id": agent_id,
                        "outcome": outcome,
                        "_audit_event": True,
                    },
                )
                return True

            if hasattr(self._store, "put_audit"):
                self._store.put_audit(
                    event_type=event_type,
                    session_id=session_id,
                    trace_id=trace_id,
                    details=details,
                    outcome=outcome,
                )
                return True

            return False
        except Exception:
            return False

    def mirror_batch(
        self,
        events: list[dict[str, Any]],
        session_id: str | None,
    ) -> int:
        if not session_id or not events:
            return 0

        count = 0
        for ev in events:
            success = self.mirror_event(
                event_type=ev.get("event_type", "unknown"),
                session_id=session_id,
                agent_id=ev.get("agent_id"),
                trace_id=ev.get("trace_id", ""),
                details=ev.get("details", {}),
                outcome=ev.get("outcome", "ok"),
            )
            if success:
                count += 1
        return count
