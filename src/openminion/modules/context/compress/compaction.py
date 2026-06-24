from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .interfaces import COMPRESS_INTERFACE_VERSION
from .events import emit_compress_counter, emit_compress_operation
from .schemas import (
    CheckpointStructuredState,
    CompressionBundle,
    CompressionCheckpoint,
    SeedBundle,
    SeedBundleBudgets,
    SeedSection,
    StructuredConstraint,
    StructuredDecision,
    StructuredOpenLoop,
    StructuredToolDigest,
    TierEntry,
    TierType,
)
from .storage.checkpoint_store import CheckpointStore
from .token_count import count_tokens
from .strategies import (
    AfterLastCheckpointSelector,
    CheckpointComposerV1,
    DeltaEvent,
    StrategyRegistry,
)


def _truncate_to_budget(text: str, max_tokens: int) -> str:
    tokens = text.split()
    if len(tokens) <= max_tokens:
        return text
    return " ".join(tokens[:max_tokens])


def _stable_id() -> str:
    return str(uuid.uuid4())


class TriggerReason:
    TOKEN_PRESSURE = "token_pressure"
    AFTER_RUN_FINISHED = "after_run_finished"
    LARGE_TOOL_OUTPUT = "large_tool_output"
    MANUAL_REFRESH = "manual_refresh"
    TASK_BOUNDARY = "task_boundary"


class TriggerPolicy:
    """Configurable trigger conditions for checkpoint/seed generation."""

    def __init__(
        self,
        *,
        token_pressure_threshold: float = 0.85,
        large_tool_output_tokens: int = 500,
        checkpoint_every_n_events: int = 50,
    ) -> None:
        self.token_pressure_threshold = token_pressure_threshold
        self.large_tool_output_tokens = large_tool_output_tokens
        self.checkpoint_every_n_events = checkpoint_every_n_events

    def evaluate(
        self,
        events: List[DeltaEvent],
        current_bundle: Optional[CompressionBundle],
        *,
        estimated_prompt_tokens: int = 0,
        budget_total_tokens: int = 0,
        manual_refresh: bool = False,
    ) -> List[str]:
        """Return list of trigger reasons that fired."""
        reasons: List[str] = []
        if manual_refresh:
            reasons.append(TriggerReason.MANUAL_REFRESH)
        if budget_total_tokens > 0 and estimated_prompt_tokens > 0:
            ratio = estimated_prompt_tokens / budget_total_tokens
            if ratio >= self.token_pressure_threshold:
                reasons.append(TriggerReason.TOKEN_PRESSURE)
        for e in events:
            if e.event_type in ("tool.completed", "tool.result"):
                text_len = count_tokens(e.text or "")
                payload_len = count_tokens(json.dumps(e.payload))
                if text_len + payload_len > self.large_tool_output_tokens:
                    if TriggerReason.LARGE_TOOL_OUTPUT not in reasons:
                        reasons.append(TriggerReason.LARGE_TOOL_OUTPUT)
            if e.event_type in ("run.finished", "turn.completed"):
                if TriggerReason.AFTER_RUN_FINISHED not in reasons:
                    reasons.append(TriggerReason.AFTER_RUN_FINISHED)
            if e.event_type in ("task.boundary", "task.completed", "task.created"):
                if TriggerReason.TASK_BOUNDARY not in reasons:
                    reasons.append(TriggerReason.TASK_BOUNDARY)
        return reasons


class BudgetArbiter:
    """Enforces hard token ceilings on SeedBundle generation."""

    def __init__(self, budgets: Optional[SeedBundleBudgets] = None) -> None:
        self.budgets = budgets or SeedBundleBudgets()

    def _cap_for_tier(self, tier_type: TierType) -> int:
        caps = {
            "summary": self.budgets.summary_max_tokens,
            "decisions": self.budgets.decisions_max_tokens,
            "constraints": self.budgets.constraints_max_tokens,
            "entities": self.budgets.entities_max_tokens,
            "open_loops": self.budgets.open_loops_max_tokens,
            "tool_digests": self.budgets.tool_digests_max_tokens,
            "failures": 50,
        }
        tier_cap = caps.get(tier_type, 100)
        return min(tier_cap, self.budgets.total_max_tokens)

    def enforce(self, sections: List[SeedSection]) -> List[SeedSection]:
        """Trim sections to fit within per-tier and total caps."""
        trimmed: List[SeedSection] = []
        total_used = 0
        for sec in sections:
            tier_cap = self._cap_for_tier(sec.section_type)
            text = _truncate_to_budget(sec.text, tier_cap)
            tokens = count_tokens(text)
            if total_used + tokens > self.budgets.total_max_tokens:
                remaining = self.budgets.total_max_tokens - total_used
                if remaining <= 0:
                    break
                text = _truncate_to_budget(text, remaining)
                tokens = count_tokens(text)
            if text.strip():
                trimmed.append(
                    SeedSection(
                        section_type=sec.section_type,
                        text=text,
                        refs=list(sec.refs),
                        token_count=tokens,
                    )
                )
                total_used += tokens
        return trimmed


def distill_tool_output(
    tool_name: str,
    raw_output: str,
    *,
    max_tokens: int = 100,
    evidence_refs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Produce a distilled summary from raw tool output."""
    words = raw_output.split()
    summary = " ".join(words[:max_tokens])
    if len(words) > max_tokens:
        summary += "..."
    return {
        "tool_name": tool_name,
        "distilled_summary": summary,
        "evidence_refs": evidence_refs or [],
        "verified": False,
        "source": "extractive",
    }


def exclude_raw_tool_output(events: List[DeltaEvent]) -> List[DeltaEvent]:
    """Strip raw tool output from events, replacing with distilled summaries."""
    cleaned: List[DeltaEvent] = []
    for e in events:
        if e.event_type in ("tool.completed", "tool.result"):
            tool_name = e.payload.get("tool_name", "unknown")
            raw = e.text or e.payload.get("output", "")
            digest = distill_tool_output(tool_name, raw, evidence_refs=list(e.refs))
            new_payload = {**e.payload, **digest}
            new_payload.pop("output", None)
            new_payload.pop("raw_output", None)
            cleaned.append(
                DeltaEvent(
                    event_id=e.event_id,
                    event_type=e.event_type,
                    payload=new_payload,
                    text=digest["distilled_summary"],
                    refs=list(e.refs),
                    meta=dict(e.meta),
                )
            )
        else:
            cleaned.append(e)
    return cleaned


class CompactionService:
    """Stateful compression service for V1.5 rollover support."""

    contract_version = COMPRESS_INTERFACE_VERSION

    def __init__(
        self,
        *,
        registry: Optional[StrategyRegistry] = None,
        trigger_policy: Optional[TriggerPolicy] = None,
        budget_arbiter: Optional[BudgetArbiter] = None,
        checkpoint_store: Optional[CheckpointStore] = None,
        sessctl: Any = None,
        telemetryctl: Any = None,
        token_limit: int = 2400,
        recent_window_min_events: int = 10,
    ) -> None:
        self._registry = registry or StrategyRegistry.default()
        self._trigger_policy = trigger_policy or TriggerPolicy()
        self._budget_arbiter = budget_arbiter or BudgetArbiter()
        self._checkpoint_store = checkpoint_store or CheckpointStore()
        self._sessctl = sessctl
        self._telemetryctl = telemetryctl
        self._token_limit = token_limit
        self._recent_window_min_events = recent_window_min_events
        self._delta_selector = AfterLastCheckpointSelector()
        self._composer = CheckpointComposerV1()
        self._bundles: Dict[str, CompressionBundle] = {}
        self._checkpoints: Dict[str, List[Dict[str, Any]]] = {}
        self._all_events: Dict[str, List[DeltaEvent]] = {}
        self._seeds: Dict[str, List[Dict[str, Any]]] = {}
        self._telemetry_turn_id: str | None = None

    def set_telemetry_context(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> None:
        del session_id
        self._telemetry_turn_id = str(turn_id or "").strip() or None

    def update(
        self,
        session_id: str,
        events: List[DeltaEvent],
    ) -> CompressionBundle:
        """Process a batch of delta events through the Extract→Abstract pipeline.

        Returns the updated ``CompressionBundle`` for the session.
        """
        if not events:
            return self._get_or_create_bundle(session_id)

        self._all_events.setdefault(session_id, []).extend(events)
        cleaned_events = exclude_raw_tool_output(events)

        current = self._get_or_create_bundle(session_id)

        all_new_entries: List[TierEntry] = []
        for strategy in self._registry.all_strategies():
            extracted = strategy.extract(cleaned_events, current)
            all_new_entries.extend(extracted)

        merged_tiers: List[TierEntry] = []
        processed_types: set[str] = set()
        for strategy in self._registry.all_strategies():
            for tier_type in strategy.tier_types:
                if tier_type in processed_types:
                    continue
                processed_types.add(tier_type)
                existing = [t for t in current.tiers if t.tier_type == tier_type]
                new = [t for t in all_new_entries if t.tier_type == tier_type]
                if not existing and not new:
                    continue
                cap = self._budget_arbiter._cap_for_tier(tier_type)
                abstracted = strategy.abstract(existing, new, cap)
                merged_tiers.extend(abstracted)

        handled_types = processed_types
        for t in current.tiers:
            if t.tier_type not in handled_types:
                merged_tiers.append(t)

        summary_entries = [t for t in merged_tiers if t.tier_type == "summary"]
        summary_text = (
            " ".join(t.text for t in summary_entries)
            if summary_entries
            else current.summary_text
        )

        last_event_id = (
            cleaned_events[-1].event_id if cleaned_events else current.up_to_event_id
        )
        total_tokens = sum(t.token_count for t in merged_tiers)

        updated = CompressionBundle(
            bundle_id=current.bundle_id,
            session_id=session_id,
            summary_text=summary_text,
            tiers=merged_tiers,
            up_to_event_id=last_event_id,
            checkpoint_id=current.checkpoint_id,
            total_tokens=total_tokens,
            version=current.version + 1,
            meta=dict(current.meta),
        )
        self._bundles[session_id] = updated

        if self._sessctl and hasattr(self._sessctl, "save_compression_checkpoint"):
            triggers = self._trigger_policy.evaluate(cleaned_events, updated)
            if triggers:
                self.checkpoint(session_id, reason="|".join(triggers))

        return updated

    def checkpoint(
        self,
        session_id: str,
        *,
        up_to_event_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> str:
        """Create a checkpoint of the current bundle state."""
        bundle = self._get_or_create_bundle(session_id)
        cp_id = _stable_id()
        event_id = up_to_event_id or bundle.up_to_event_id

        cp = {
            "checkpoint_id": cp_id,
            "session_id": session_id,
            "bundle": bundle,
            "bundle_json": json.dumps(asdict(bundle), sort_keys=True),
            "up_to_event_id": event_id,
            "reason": reason,
        }
        self._checkpoints.setdefault(session_id, []).append(cp)

        self._bundles[session_id] = CompressionBundle(
            bundle_id=bundle.bundle_id,
            session_id=session_id,
            summary_text=bundle.summary_text,
            tiers=list(bundle.tiers),
            up_to_event_id=bundle.up_to_event_id,
            checkpoint_id=cp_id,
            total_tokens=bundle.total_tokens,
            version=bundle.version,
            meta=dict(bundle.meta),
        )

        if self._sessctl and hasattr(self._sessctl, "save_compression_checkpoint"):
            self._sessctl.save_compression_checkpoint(
                session_id,
                cp["bundle_json"],
                up_to_event_id=event_id,
                reason=reason,
            )

        return cp_id

    def get_latest(self, session_id: str) -> CompressionBundle:
        """Return the current bundle for a session."""
        return self._get_or_create_bundle(session_id)

    def get_snapshot(
        self,
        *,
        session_id: str,
        agent_id: str | None = None,
        mode_name: str | None = None,
    ) -> str | None:
        """Return compressed summary text for context pipeline snapshot consumption."""
        del agent_id, mode_name
        bundle = self._bundles.get(session_id)
        if bundle is None:
            return None
        text = str(bundle.summary_text or "").strip()
        return text or None

    def build_rollover_seed(
        self,
        session_id: str,
        *,
        k_entities: int = 20,
        k_tools: int = 10,
        budgets: Optional[SeedBundleBudgets] = None,
    ) -> SeedBundle:
        """Build a SeedBundle from the current bundle for prompt rollover."""
        bundle = self._get_or_create_bundle(session_id)
        return self._build_seed_from_bundle(
            bundle,
            k_entities=k_entities,
            k_tools=k_tools,
            budgets=budgets,
        )

    def build_rollover_seed_from_checkpoint(
        self,
        checkpoint_id: str,
        *,
        k_entities: int = 20,
        k_tools: int = 10,
        budgets: Optional[SeedBundleBudgets] = None,
    ) -> SeedBundle:
        """Build a SeedBundle from a specific checkpoint."""
        bundle = self._find_checkpoint_bundle(checkpoint_id)
        if bundle is None:
            raise ValueError(f"checkpoint not found: {checkpoint_id}")
        return self._build_seed_from_bundle(
            bundle,
            k_entities=k_entities,
            k_tools=k_tools,
            budgets=budgets,
        )

    def evaluate_triggers(
        self,
        session_id: str,
        events: List[DeltaEvent],
        *,
        estimated_prompt_tokens: int = 0,
        budget_total_tokens: int = 0,
        manual_refresh: bool = False,
    ) -> List[str]:
        """Evaluate trigger conditions without modifying state."""
        bundle = self._bundles.get(session_id)
        return self._trigger_policy.evaluate(
            events,
            bundle,
            estimated_prompt_tokens=estimated_prompt_tokens,
            budget_total_tokens=budget_total_tokens,
            manual_refresh=manual_refresh,
        )

    def maybe_checkpoint(
        self,
        session_id: str,
        reason: str,
        until_event_id: Optional[str] = None,
    ) -> Optional[str]:
        """Create a ``CompressionCheckpoint`` if the pipeline succeeds."""
        from .schemas import CheckpointFailedPayload as _FailedPayload

        bundle = self._get_or_create_bundle(session_id)
        created_at = datetime.now(timezone.utc).isoformat()
        checkpoint_id = str(uuid.uuid4())

        latest_cp = self._checkpoint_store.get_latest_checkpoint(session_id)
        from_event_id = latest_cp.to_event_id if latest_cp else None
        to_event_id = until_event_id or bundle.up_to_event_id or checkpoint_id
        turn_id = (
            str(self._telemetry_turn_id or "").strip()
            or str(to_event_id or "").strip()
            or checkpoint_id
        )

        structured = self._build_structured_state(bundle)
        all_evs = self._all_events.get(session_id, [])
        recent_ids = [e.event_id for e in all_evs[-self._recent_window_min_events :]]
        covered_events = len(all_evs)
        if from_event_id:
            for index, event in enumerate(all_evs):
                if event.event_id == from_event_id:
                    covered_events = max(0, len(all_evs[index + 1 :]))
                    break
        covered_tokens = max(0, count_tokens(str(bundle.summary_text or "")))
        event_extra = {
            "reason": str(reason or "").strip().lower() or "checkpoint",
            "covered_events": covered_events,
            "covered_tokens": covered_tokens,
            "trace_id": turn_id,
        }

        if not str(bundle.summary_text or "").strip():
            emit_compress_operation(
                telemetryctl=self._telemetryctl,
                session_id=session_id,
                turn_id=turn_id,
                operation="summary_skip",
                status="ok",
                extra=event_extra,
            )
            emit_compress_counter(
                telemetryctl=self._telemetryctl,
                session_id=session_id,
                turn_id=turn_id,
                counter_name="covered_events",
                value=float(max(0, covered_events)),
                extra=event_extra,
            )
            emit_compress_counter(
                telemetryctl=self._telemetryctl,
                session_id=session_id,
                turn_id=turn_id,
                counter_name="covered_tokens",
                value=float(max(0, covered_tokens)),
                extra=event_extra,
            )
            return None

        result = self._composer.compose(
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            created_at=created_at,
            from_event_id=from_event_id,
            to_event_id=to_event_id,
            summary_text=bundle.summary_text,
            recent_window_event_ids=recent_ids,
            structured=structured,
            token_limit=self._token_limit,
        )

        if isinstance(result, _FailedPayload):
            emit_compress_operation(
                telemetryctl=self._telemetryctl,
                session_id=session_id,
                turn_id=turn_id,
                operation="summary_error",
                status="error",
                extra={**event_extra, "error_code": str(result.error_code or "")},
            )
            self._checkpoint_store.record_failure(result)
            if self._sessctl and hasattr(self._sessctl, "write_event"):
                self._sessctl.write_event(
                    session_id,
                    "compression.checkpoint.failed",
                    result.to_dict(),
                )
            return None

        self._checkpoint_store.save_checkpoint(result)
        emit_compress_operation(
            telemetryctl=self._telemetryctl,
            session_id=session_id,
            turn_id=turn_id,
            operation="summary_refresh" if latest_cp is not None else "summary_create",
            status="ok",
            extra=event_extra,
        )
        emit_compress_counter(
            telemetryctl=self._telemetryctl,
            session_id=session_id,
            turn_id=turn_id,
            counter_name="covered_events",
            value=float(max(0, covered_events)),
            extra=event_extra,
        )
        emit_compress_counter(
            telemetryctl=self._telemetryctl,
            session_id=session_id,
            turn_id=turn_id,
            counter_name="covered_tokens",
            value=float(max(0, covered_tokens)),
            extra=event_extra,
        )

        cp_record = {
            "checkpoint_id": checkpoint_id,
            "session_id": session_id,
            "bundle": bundle,
            "bundle_json": json.dumps(asdict(bundle), sort_keys=True),
            "up_to_event_id": to_event_id,
            "reason": reason,
        }
        self._checkpoints.setdefault(session_id, []).append(cp_record)

        self._bundles[session_id] = CompressionBundle(
            bundle_id=bundle.bundle_id,
            session_id=session_id,
            summary_text=bundle.summary_text,
            tiers=list(bundle.tiers),
            up_to_event_id=bundle.up_to_event_id,
            checkpoint_id=checkpoint_id,
            total_tokens=bundle.total_tokens,
            version=bundle.version,
            meta=dict(bundle.meta),
        )

        if self._sessctl and hasattr(self._sessctl, "save_compression_checkpoint"):
            self._sessctl.save_compression_checkpoint(
                session_id,
                cp_record["bundle_json"],
                up_to_event_id=to_event_id,
                reason=reason,
            )
        if self._sessctl and hasattr(self._sessctl, "write_event"):
            self._sessctl.write_event(
                session_id,
                "compression.checkpoint.created",
                result.to_dict(),
            )

        return checkpoint_id

    def get_latest_checkpoint(self, session_id: str) -> Optional[CompressionCheckpoint]:
        """Return the most recent ``CompressionCheckpoint`` for a session (C15-015)."""
        return self._checkpoint_store.get_latest_checkpoint(session_id)

    def get_structured_state(self, session_id: str) -> Dict[str, Any]:
        """Return current structured state extracted from the session bundle (C15-016).

        Returns a dict with keys: decisions, constraints, open_loops, entities, tool_digests.
        """
        bundle = self._get_or_create_bundle(session_id)
        structured = self._build_structured_state(bundle)
        return {
            "decisions": [d.__dict__ for d in structured.decisions],
            "constraints": [c.__dict__ for c in structured.constraints],
            "open_loops": [o.__dict__ for o in structured.open_loops],
            "entities": dict(structured.entities),
            "tool_digests": [t.__dict__ for t in structured.tool_digests],
        }

    def build_seed_bundle(
        self,
        session_id: str,
        budget_tokens: int = 1200,
    ) -> SeedBundle:
        """Build a ``SeedBundle`` from the latest checkpoint or current bundle (C15-017).

        This is the spec-aligned alias for ``build_rollover_seed()``.
        """
        budgets = SeedBundleBudgets(total_max_tokens=budget_tokens)
        return self.build_rollover_seed(session_id, budgets=budgets)

    def rebuild_checkpoints(
        self,
        session_id: str,
        events: Optional[List[DeltaEvent]] = None,
    ) -> List[str]:
        """Recompute checkpoints from raw session events (C15-019)."""
        existing = self._checkpoint_store.list_checkpoints(session_id)
        for cp in existing:
            self._checkpoint_store.delete_checkpoint(cp.checkpoint_id)

        self._bundles.pop(session_id, None)

        replay_events = (
            events if events is not None else self._all_events.get(session_id, [])
        )

        if not replay_events:
            return []

        self.update(session_id, replay_events)

        cp_id = self.maybe_checkpoint(session_id, reason="rebuild")
        return [cp_id] if cp_id else []

    def _build_structured_state(
        self, bundle: CompressionBundle
    ) -> CheckpointStructuredState:
        decisions: List[StructuredDecision] = []
        constraints: List[StructuredConstraint] = []
        open_loops: List[StructuredOpenLoop] = []
        entities: Dict[str, Any] = {}
        tool_digests: List[StructuredToolDigest] = []

        for tier in bundle.tiers:
            if tier.tier_type == "decisions":
                item_id = tier.meta.get("stable_id") or str(
                    uuid.uuid5(uuid.NAMESPACE_DNS, tier.text)
                )
                decisions.append(
                    StructuredDecision(
                        id=item_id,
                        statement=tier.text,
                        evidence_refs=list(tier.refs),
                    )
                )
            elif tier.tier_type == "constraints":
                item_id = tier.meta.get("stable_id") or str(
                    uuid.uuid5(uuid.NAMESPACE_DNS, tier.text)
                )
                constraints.append(
                    StructuredConstraint(
                        id=item_id,
                        statement=tier.text,
                        scope=tier.meta.get("scope", "global"),
                        priority=tier.meta.get("priority", "normal"),
                    )
                )
            elif tier.tier_type == "open_loops":
                item_id = tier.meta.get("stable_id") or str(
                    uuid.uuid5(uuid.NAMESPACE_DNS, tier.text)
                )
                open_loops.append(
                    StructuredOpenLoop(
                        id=item_id,
                        question_or_todo=tier.text,
                        owner=tier.meta.get("owner"),
                        status=tier.meta.get("status", "open"),
                    )
                )
            elif tier.tier_type == "entities":
                entities[tier.text.strip()] = {"refs": list(tier.refs)}
            elif tier.tier_type == "tool_digests":
                tool_name = tier.meta.get("tool_name", "unknown")
                tool_digests.append(
                    StructuredToolDigest(
                        tool_name=tool_name,
                        outcome=tier.text,
                        artifact_refs=list(tier.refs),
                    )
                )

        return CheckpointStructuredState(
            decisions=decisions,
            constraints=constraints,
            open_loops=open_loops,
            entities=entities,
            tool_digests=tool_digests,
        )

    def _get_or_create_bundle(self, session_id: str) -> CompressionBundle:
        if session_id not in self._bundles:
            if self._sessctl and hasattr(self._sessctl, "get_latest_checkpoint"):
                cp = self._sessctl.get_latest_checkpoint(session_id)
                if cp and cp.get("bundle_json"):
                    try:
                        data = json.loads(cp["bundle_json"])
                        tiers = [TierEntry(**t) for t in data.get("tiers", [])]
                        bundle = CompressionBundle(
                            bundle_id=data.get("bundle_id", _stable_id()),
                            session_id=session_id,
                            summary_text=data.get("summary_text", ""),
                            tiers=tiers,
                            up_to_event_id=data.get("up_to_event_id"),
                            checkpoint_id=cp.get("checkpoint_id"),
                            total_tokens=data.get("total_tokens", 0),
                            version=data.get("version", 1),
                            meta=data.get("meta", {}),
                        )
                        self._bundles[session_id] = bundle
                        return bundle
                    except (json.JSONDecodeError, TypeError, KeyError):
                        pass
            self._bundles[session_id] = CompressionBundle(
                bundle_id=_stable_id(),
                session_id=session_id,
                summary_text="",
            )
        return self._bundles[session_id]

    def _build_seed_from_bundle(
        self,
        bundle: CompressionBundle,
        *,
        k_entities: int = 20,
        k_tools: int = 10,
        budgets: Optional[SeedBundleBudgets] = None,
    ) -> SeedBundle:
        effective_budgets = budgets or self._budget_arbiter.budgets
        arbiter = BudgetArbiter(effective_budgets)

        sections: List[SeedSection] = []

        if bundle.summary_text.strip():
            sections.append(
                SeedSection(
                    section_type="summary",
                    text=bundle.summary_text,
                    token_count=count_tokens(bundle.summary_text),
                )
            )

        tier_order: List[TierType] = [
            "decisions",
            "constraints",
            "open_loops",
            "entities",
            "tool_digests",
            "failures",
        ]
        for tier_type in tier_order:
            entries = [t for t in bundle.tiers if t.tier_type == tier_type]
            if not entries:
                continue
            if tier_type == "entities":
                entries = entries[:k_entities]
            elif tier_type == "tool_digests":
                entries = entries[:k_tools]
            text = "\n".join(e.text for e in entries if e.text.strip())
            refs = [r for e in entries for r in e.refs]
            if text.strip():
                sections.append(
                    SeedSection(
                        section_type=tier_type,
                        text=text,
                        refs=refs,
                        token_count=count_tokens(text),
                    )
                )

        sections = arbiter.enforce(sections)
        total_tokens = sum(s.token_count for s in sections)

        seed = SeedBundle(
            seed_id=_stable_id(),
            session_id=bundle.session_id,
            source_bundle_id=bundle.bundle_id,
            source_checkpoint_id=bundle.checkpoint_id,
            sections=sections,
            total_tokens=total_tokens,
            budgets=effective_budgets,
            up_to_event_id=bundle.up_to_event_id,
        )

        if self._sessctl and hasattr(self._sessctl, "save_seed_bundle"):
            sections_data = [asdict(s) for s in sections]
            self._sessctl.save_seed_bundle(
                bundle.session_id,
                bundle.bundle_id,
                json.dumps(sections_data, sort_keys=True),
                total_tokens,
                source_checkpoint_id=bundle.checkpoint_id,
                budgets_json=json.dumps(asdict(effective_budgets), sort_keys=True),
                up_to_event_id=bundle.up_to_event_id,
            )

        return seed

    def _find_checkpoint_bundle(
        self, checkpoint_id: str
    ) -> Optional[CompressionBundle]:
        for cps in self._checkpoints.values():
            for cp in cps:
                if cp["checkpoint_id"] == checkpoint_id:
                    return cp["bundle"]
        if self._sessctl and hasattr(self._sessctl, "get_latest_checkpoint"):
            pass
        return None
