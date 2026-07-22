from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any
from collections.abc import Callable, Mapping

from openminion.base.time import utc_now_iso as _utc_now_iso
from openminion.modules.storage.record_store import RecordStore

from .context import ContextStore, RunStore
from .cron_store import CronStore
from .event_writes import SessionEventWriter
from .events import EventStore
from .replay import SessionReplayHelper
from .sessions import SessionLifecycleHelper
from .queries import SessionSliceQueries
from .slices import SessionSliceSourceAdapter, SliceStore
from .summaries import StateStore, SummaryStore
from .turn_leases import SessionTurnLeaseStore


@dataclass(frozen=True)
class StoreComponents:
    event_store: EventStore
    slice_queries: SessionSliceQueries
    event_writer: SessionEventWriter
    cron_store: CronStore
    state_store: StateStore
    summary_store: SummaryStore
    context_store: ContextStore
    run_store: RunStore
    session_helper: SessionLifecycleHelper
    replay_helper: SessionReplayHelper
    slice_store: SliceStore
    turn_lease_store: SessionTurnLeaseStore


def _build_slice_source_adapter(
    *,
    get_session: Callable[[str], dict[str, Any] | None],
    slice_queries: SessionSliceQueries,
    event_store: EventStore,
    state_store: StateStore,
    summary_store: SummaryStore,
    context_store: ContextStore,
) -> SessionSliceSourceAdapter:
    return SessionSliceSourceAdapter(
        session_getter=get_session,
        latest_event_seq_getter=slice_queries.latest_event_seq_tx,
        summary_getter=summary_store.get_summary,
        recent_turns_getter=event_store.get_recent_turns,
        total_turn_count_getter=event_store.get_total_turn_count,
        conversation_summary_getter=event_store.get_conversation_summary,
        active_task_plan_getter=event_store.get_active_task_plan,
        continuation_projection_getter=event_store.get_latest_continuation_projection,
        pending_trailer_feedback_getter=event_store.get_pending_trailer_feedback,
        open_tasks_getter=slice_queries.derive_open_tasks,
        active_state_getter=state_store.get_active_state,
        recent_tool_events_getter=event_store.get_recent_tool_events,
        prompt_context_getter=context_store.get_active_prompt_context,
        checkpoint_getter=context_store.get_latest_checkpoint,
        seed_bundle_getter=context_store.get_latest_seed_bundle,
        archive_refs_getter=slice_queries.list_recent_archive_ref_lines,
    )


def build_store_components(
    *,
    record_store: RecordStore,
    lock: RLock,
    slice_cache: dict[tuple[str, str, str, int], dict[str, Any]],
    get_session: Callable[[str], dict[str, Any] | None],
    append_event: Callable[..., str],
    touch_session_tx: Callable[..., None],
    invalidate_slice_cache: Callable[[str], None],
    add_artifact_refs: Callable[..., None],
    latest_event_seq: Callable[[str], int],
    normalize_limits: Callable[[Any], Mapping[str, Any]],
    stable_hash: Callable[[Any], str],
) -> StoreComponents:
    event_store = EventStore(record_store)
    slice_queries = SessionSliceQueries(record_store, lock=lock)
    run_store = RunStore(record_store, utc_now_iso=_utc_now_iso)
    event_writer = SessionEventWriter(
        record_store,
        event_store=event_store,
        get_session=get_session,
        touch_session_tx=touch_session_tx,
        invalidate_slice_cache=invalidate_slice_cache,
        add_artifact_refs=add_artifact_refs,
        add_run_usage_delta=run_store.add_run_usage_delta,
        utc_now_iso=_utc_now_iso,
    )
    cron_store = CronStore(record_store, lock)
    turn_lease_store = SessionTurnLeaseStore(record_store, lock)
    state_store = StateStore(
        record_store,
        touch_session_tx=touch_session_tx,
        invalidate_slice_cache=invalidate_slice_cache,
        utc_now_iso=_utc_now_iso,
    )
    summary_store = SummaryStore(
        record_store,
        touch_session_tx=touch_session_tx,
        invalidate_slice_cache=invalidate_slice_cache,
        latest_event_seq_tx=slice_queries.latest_event_seq_tx,
        derive_open_tasks=slice_queries.derive_open_tasks,
        append_event=append_event,
        get_latest_working_state=state_store.get_latest_working_state,
        utc_now_iso=_utc_now_iso,
    )
    context_store = ContextStore(record_store, utc_now_iso=_utc_now_iso)
    session_helper = SessionLifecycleHelper(
        record_store=record_store,
        lock=lock,
        invalidate_slice_cache=invalidate_slice_cache,
        append_event=append_event,
        utc_now_iso=_utc_now_iso,
    )
    replay_helper = SessionReplayHelper(
        record_store=record_store,
        lock=lock,
        get_session=get_session,
        get_active_prompt_context=context_store.get_active_prompt_context,
        get_latest_checkpoint=context_store.get_latest_checkpoint,
        get_latest_seed_bundle=context_store.get_latest_seed_bundle,
        latest_event_seq=latest_event_seq,
        get_latest_working_state=state_store.get_latest_working_state,
        append_event=append_event,
    )
    slice_store = SliceStore(
        _build_slice_source_adapter(
            get_session=get_session,
            slice_queries=slice_queries,
            event_store=event_store,
            state_store=state_store,
            summary_store=summary_store,
            context_store=context_store,
        ),
        lock=lock,
        slice_cache=slice_cache,
        normalize_limits=normalize_limits,
        stable_hash=stable_hash,
    )
    return StoreComponents(
        event_store=event_store,
        slice_queries=slice_queries,
        event_writer=event_writer,
        cron_store=cron_store,
        state_store=state_store,
        summary_store=summary_store,
        context_store=context_store,
        run_store=run_store,
        session_helper=session_helper,
        replay_helper=replay_helper,
        slice_store=slice_store,
        turn_lease_store=turn_lease_store,
    )
