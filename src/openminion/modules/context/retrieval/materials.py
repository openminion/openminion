"""Retrieved-material collection helpers for ``ContextCtlService``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import (
    CONTEXT_MID_SESSION_RECALL_INTERVAL as _MID_SESSION_RECALL_INTERVAL,
    CONTEXT_MID_SESSION_RECALL_LIMIT as _MID_SESSION_RECALL_LIMIT,
    CONTEXT_RECENT_SESSION_ARTIFACT_LIMIT as _RECENT_SESSION_ARTIFACT_LIMIT,
    CONTEXT_RECENT_SESSION_ARTIFACT_MAX_AGE_DAYS as _RECENT_SESSION_ARTIFACT_MAX_AGE_DAYS,
    CONTEXT_SESSION_START_RECALL_LIMIT as _SESSION_START_RECALL_LIMIT,
)
from ..schemas import (
    ArtifactDigest,
    BuildConstraints,
    BuildPackRequest,
    ContextBudgets,
    ContextManifest,
    FactRecord,
    MemoryCard,
    MidSessionIntentSnapshot,
    MidSessionRecallSnapshot,
    RecentSessionArtifactRef,
    SessionSlice,
    SkillSnippetRef,
)


def _latest_user_message(session_slice: SessionSlice) -> str:
    for turn in reversed(list(session_slice.recent_turns or [])):
        if str(getattr(turn, "role", "") or "").strip().lower() != "user":
            continue
        content = str(getattr(turn, "content", "") or "").strip()
        if content:
            return content
    return ""


def _dedupe_memory_cards(cards: list[MemoryCard]) -> list[MemoryCard]:
    seen: set[str] = set()
    deduped: list[MemoryCard] = []
    for card in cards:
        record_id = str(card.record_id or "").strip()
        if not record_id or record_id in seen:
            continue
        seen.add(record_id)
        deduped.append(card)
    return deduped


def _safe_memory_call(call: Any, **kwargs: Any) -> list[Any]:
    try:
        return call(**kwargs)
    except Exception:
        return []


@dataclass(frozen=True)
class _RetrievedContextMaterials:
    fact_records: list[FactRecord]
    memory_cards: list[MemoryCard]
    prior_manifest: ContextManifest | None
    mid_session_recall_state: MidSessionRecallSnapshot
    session_start_recalled_memory_cards: list[MemoryCard]
    recent_session_artifact_refs: list[RecentSessionArtifactRef]
    mid_session_recalled_memory_cards: list[MemoryCard]
    procedure: Any
    skill_snippet_text: str | None
    skill_segment_id: str | None
    artifact_digests: list[ArtifactDigest]


class RetrievedContextMaterialsCollector:
    """Internal owner for retrieved context materials and recall state."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def collect_retrieved_context_materials(
        self,
        *,
        request: BuildPackRequest,
        constraints: BuildConstraints,
        budgets: ContextBudgets,
        session_slice: SessionSlice,
    ) -> _RetrievedContextMaterials:
        fact_records = self._service._memctl.query_facts(  # noqa: SLF001
            session_id=request.session_id,
            agent_id=request.agent_id,
            query=request.query,
            limit=20,
            mode_name=request.mode_name,
        )
        memory_cards = self._service._memctl.query_memory_cards(  # noqa: SLF001
            session_id=request.session_id,
            agent_id=request.agent_id,
            query=request.query,
            limit=15,
            mode_name=request.mode_name,
        )
        prior_manifest = self._service._latest_manifest_by_session.get(  # noqa: SLF001
            request.session_id
        )
        session_turn_index = int(
            session_slice.total_turn_count or len(session_slice.recent_turns)
        )
        mid_session_recall_state = self.build_mid_session_recall_state(
            session_slice=session_slice,
            turn_index=session_turn_index,
        )
        session_start_recalled_memory_cards = self.recall_session_start_memory(
            request=request,
            turn_index=session_turn_index,
        )
        recent_session_artifact_refs = self.recall_recent_session_artifacts(
            request=request,
            turn_index=session_turn_index,
        )
        mid_session_recalled_memory_cards = self.recall_mid_session_memory(
            request=request,
            session_slice=session_slice,
            recall_state=mid_session_recall_state,
            prior_manifest=prior_manifest,
        )
        recalled_memory_cards = _dedupe_memory_cards(
            [
                *session_start_recalled_memory_cards,
                *mid_session_recalled_memory_cards,
            ]
        )
        if recalled_memory_cards:
            memory_cards = _dedupe_memory_cards([*recalled_memory_cards, *memory_cards])
        skill_snippet_text, skill_segment_id = self.resolve_skill_snippet(
            constraints=constraints,
            purpose=request.purpose,
            mode_name=request.mode_name,
            skills_tokens=budgets.skills_tokens,
        )
        procedure = None
        if constraints.procedure_id:
            procedure = self._service._memctl.get_procedure(  # noqa: SLF001
                procedure_id=constraints.procedure_id
            )
        artifact_digests = self._service._artifactctl.query_digests(  # noqa: SLF001
            session_id=request.session_id,
            agent_id=request.agent_id,
            query=request.query,
            limit=10,
        )
        return _RetrievedContextMaterials(
            fact_records=fact_records,
            memory_cards=memory_cards,
            prior_manifest=prior_manifest,
            mid_session_recall_state=mid_session_recall_state,
            session_start_recalled_memory_cards=session_start_recalled_memory_cards,
            recent_session_artifact_refs=recent_session_artifact_refs,
            mid_session_recalled_memory_cards=mid_session_recalled_memory_cards,
            procedure=procedure,
            skill_snippet_text=skill_snippet_text,
            skill_segment_id=skill_segment_id,
            artifact_digests=artifact_digests,
        )

    def resolve_skill_snippet(
        self,
        *,
        constraints: BuildConstraints,
        purpose: str,
        mode_name: str | None,
        skills_tokens: int,
    ) -> tuple[str | None, str | None]:
        skill_snippet_text = None
        skill_segment_id = constraints.skill_id
        skill_refs = list(constraints.skill_refs or [])
        if not skill_refs and constraints.skill_id:
            skill_refs = [
                SkillSnippetRef(
                    skill_id=constraints.skill_id,
                    version_hash=constraints.skill_version_hash,
                )
            ]
        if skill_refs and self._service._skillctl is not None:  # noqa: SLF001
            snippet_parts: list[str] = []
            per_skill_budget = max(80, int(skills_tokens / max(1, len(skill_refs))))
            for ref in skill_refs:
                try:
                    text, _ = self._service._skillctl.render_snippet(  # noqa: SLF001
                        skill_id=ref.skill_id,
                        version_hash=ref.version_hash,
                        purpose=purpose,
                        max_tokens=per_skill_budget,
                        mode_name=mode_name,
                    )
                except Exception:
                    continue
                if text and text.strip():
                    snippet_parts.append(text)
            if snippet_parts:
                skill_snippet_text = "\n\n".join(snippet_parts)
                skill_segment_id = "+".join(ref.skill_id for ref in skill_refs)
        return skill_snippet_text, skill_segment_id

    def recall_session_start_memory(
        self,
        *,
        request: BuildPackRequest,
        turn_index: int,
    ) -> list[MemoryCard]:
        if int(turn_index or 0) != 0:
            return []
        return _safe_memory_call(
            self._service._memctl.recall_session_start_memory,  # noqa: SLF001
            session_id=request.session_id,
            agent_id=request.agent_id,
            query=request.query,
            turn_index=turn_index,
            limit=_SESSION_START_RECALL_LIMIT,
            mode_name=request.mode_name,
        )

    def build_mid_session_recall_state(
        self,
        *,
        session_slice: SessionSlice,
        turn_index: int,
    ) -> MidSessionRecallSnapshot:
        active_state = (
            session_slice.active_state
            if isinstance(session_slice.active_state, dict)
            else {}
        )
        intent_states: list[MidSessionIntentSnapshot] = []
        for item in list(active_state.get("intent_execution_states", []) or []):
            if not isinstance(item, dict):
                continue
            intent_id = str(item.get("intent_id", "") or "").strip()
            status = str(item.get("status", "") or "").strip()
            if not intent_id and not status:
                continue
            intent_states.append(
                MidSessionIntentSnapshot(
                    intent_id=intent_id,
                    status=status,
                )
            )
        plan_step_ids: list[str] = []
        plan_raw = active_state.get("plan")
        if isinstance(plan_raw, dict):
            for step in list(plan_raw.get("steps", []) or []):
                if not isinstance(step, dict):
                    continue
                command_id = str(step.get("command_id", "") or "").strip()
                if command_id:
                    plan_step_ids.append(command_id)
        recent_tool_families: list[str] = []
        seen_families: set[str] = set()
        for event in list(session_slice.recent_tool_events or []):
            tool_name = str(getattr(event, "tool_name", "") or "").strip()
            if not tool_name:
                continue
            family = tool_name.split(".", 1)[0].strip()
            if not family or family in seen_families:
                continue
            seen_families.add(family)
            recent_tool_families.append(family)
        try:
            plan_cursor = int(active_state.get("cursor", 0) or 0)
        except Exception:
            plan_cursor = 0
        resolved_skill_ids = [
            str(item).strip()
            for item in list(active_state.get("resolved_skill_ids", []) or [])
            if str(item).strip()
        ]
        active_skill_id = str(active_state.get("active_skill_id", "") or "").strip()
        return MidSessionRecallSnapshot(
            turn_index=max(0, int(turn_index or 0)),
            intent_states=intent_states,
            latest_user_message=_latest_user_message(session_slice),
            active_skill_id=active_skill_id or None,
            resolved_skill_ids=resolved_skill_ids,
            plan_cursor=max(0, plan_cursor),
            plan_step_ids=plan_step_ids,
            recent_tool_families=recent_tool_families,
        )

    def recall_recent_session_artifacts(
        self,
        *,
        request: BuildPackRequest,
        turn_index: int,
    ) -> list[RecentSessionArtifactRef]:
        if int(turn_index or 0) != 0:
            return []
        return _safe_memory_call(
            self._service._memctl.recall_recent_session_artifacts,  # noqa: SLF001
            session_id=request.session_id,
            agent_id=request.agent_id,
            max_results=_RECENT_SESSION_ARTIFACT_LIMIT,
            max_session_age=_RECENT_SESSION_ARTIFACT_MAX_AGE_DAYS,
            mode_name=request.mode_name,
        )

    def _should_recall_mid_session_memory(
        self,
        *,
        session_slice: SessionSlice,
        recall_state: MidSessionRecallSnapshot,
        prior_manifest: ContextManifest | None,
    ) -> bool:
        turn_index = int(recall_state.turn_index or 0)
        if turn_index <= 0:
            return False
        if turn_index % _MID_SESSION_RECALL_INTERVAL == 0:
            return True
        if prior_manifest is None or prior_manifest.mid_session_recall_state is None:
            return False
        prior_state = prior_manifest.mid_session_recall_state
        return (
            prior_state.intent_states != recall_state.intent_states
            or prior_state.plan_cursor != recall_state.plan_cursor
            or prior_state.plan_step_ids != recall_state.plan_step_ids
            or prior_state.active_skill_id != recall_state.active_skill_id
            or prior_state.resolved_skill_ids != recall_state.resolved_skill_ids
            or prior_state.recent_tool_families != recall_state.recent_tool_families
        )

    def recall_mid_session_memory(
        self,
        *,
        request: BuildPackRequest,
        session_slice: SessionSlice,
        recall_state: MidSessionRecallSnapshot,
        prior_manifest: ContextManifest | None,
    ) -> list[MemoryCard]:
        if not self._should_recall_mid_session_memory(
            session_slice=session_slice,
            recall_state=recall_state,
            prior_manifest=prior_manifest,
        ):
            return []
        return _safe_memory_call(
            self._service._memctl.recall_mid_session_memory,  # noqa: SLF001
            session_id=request.session_id,
            agent_id=request.agent_id,
            turn_index=recall_state.turn_index,
            latest_user_message=recall_state.latest_user_message,
            intent_ids=[
                item.intent_id for item in recall_state.intent_states if item.intent_id
            ],
            intent_statuses=[
                item.status for item in recall_state.intent_states if item.status
            ],
            active_skill_id=recall_state.active_skill_id,
            resolved_skill_ids=list(recall_state.resolved_skill_ids),
            plan_cursor=recall_state.plan_cursor,
            plan_step_ids=list(recall_state.plan_step_ids),
            recent_tool_families=list(recall_state.recent_tool_families),
            limit=_MID_SESSION_RECALL_LIMIT,
            mode_name=request.mode_name,
        )
