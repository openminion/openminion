from dataclasses import dataclass

from ..schemas import _stable_hash


@dataclass
class SummaryDelta:
    session_id: str
    seq: int
    delta_ref: str
    content: str


class ContextSummaryState:
    def __init__(self, *, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._summary_base: dict[str, str] = {}
        self._summary_deltas: dict[str, list[SummaryDelta]] = {}

    def make_delta(
        self, *, session_id: str, agent_id: str, content: str
    ) -> SummaryDelta:
        if not self._enabled:
            delta_ref = _stable_hash(
                {
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "seq": 0,
                    "disabled": True,
                }
            )
            return SummaryDelta(
                session_id=session_id,
                seq=0,
                delta_ref=delta_ref,
                content="",
            )

        deltas = self._summary_deltas.setdefault(session_id, [])
        seq = len(deltas) + 1
        delta_ref = _stable_hash(
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "seq": seq,
                "content": content,
            }
        )
        delta = SummaryDelta(
            session_id=session_id,
            seq=seq,
            delta_ref=delta_ref,
            content=content,
        )
        deltas.append(delta)
        return delta

    def maybe_compact(self, session_id: str, *, threshold: int = 5) -> bool:
        if not self._enabled:
            return False
        deltas = self._summary_deltas.get(session_id, [])
        if len(deltas) < threshold:
            return False
        combined = "\n".join(d.content for d in deltas)
        self._summary_base[session_id] = combined
        self._summary_deltas[session_id] = []
        return True

    def get_summary_base(self, session_id: str) -> str | None:
        return self._summary_base.get(session_id)

    def get_summary_deltas(self, session_id: str) -> list[SummaryDelta]:
        return list(self._summary_deltas.get(session_id, []))
