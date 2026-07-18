from __future__ import annotations

import hashlib
import logging
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from openminion.base.config.env import (
    EnvironmentConfig,
    resolve_environment_config_with_explicit_env,
)
from openminion.modules.config import (
    is_module_standalone_mode,
    resolve_module_data_root,
    resolve_module_home_root,
)
from ..interfaces import SESSION_INTERFACE_VERSION
from openminion.modules.storage.migrations.module_ids import module_id_from_package
from openminion.modules.storage.runtime.module_integrity import (
    verify_module_integrity,
)
from .base import SessionStore
from .component_wiring import build_store_components
from .context import RunStore
from .json_utils import to_json
from .migrations import MIGRATIONS, list_migrations
from .rows import row_to_session_event
from .queries import (
    _CLOSED_TASK_STATUSES as _SLICE_CLOSED_TASK_STATUSES,
)
from .components import (
    _list_recent_archive_ref_lines as _list_recent_archive_ref_lines_facade,
    acquire_cron_runs as _acquire_cron_runs_facade,
    add_cron_job as _add_cron_job_facade,
    add_message_ref as _add_message_ref_facade,
    add_run_usage_delta as _add_run_usage_delta_facade,
    close_prompt_context as _close_prompt_context_facade,
    create_prompt_context as _create_prompt_context_facade,
    create_run_record as _create_run_record_facade,
    create_snapshot as _create_snapshot_facade,
    delete_cron_job as _delete_cron_job_facade,
    delete_old_cron_runs as _delete_old_cron_runs_facade,
    emit_canonical_event as _emit_canonical_event_facade,
    enqueue_due_cron_runs as _enqueue_due_cron_runs_facade,
    enforce_context_manifest as _enforce_context_manifest_facade,
    finish_cron_run as _finish_cron_run_facade,
    finish_run_record as _finish_run_record_facade,
    get_active_prompt_context as _get_active_prompt_context_facade,
    get_cron_job as _get_cron_job_facade,
    get_latest_checkpoint as _get_latest_checkpoint_facade,
    get_latest_seed_bundle as _get_latest_seed_bundle_facade,
    get_replay_events as _get_replay_events_facade,
    get_resume_state as _get_resume_state_facade,
    get_run_record as _get_run_record_facade,
    get_slice as _get_slice_facade,
    list_cron_jobs as _list_cron_jobs_facade,
    list_cron_runs as _list_cron_runs_facade,
    list_run_records as _list_run_records_facade,
    mark_cron_delivery_target as _mark_cron_delivery_target_facade,
    reindex_sidecars as _reindex_sidecars_facade,
    renew_cron_run_lease as _renew_cron_run_lease_facade,
    replace_cron_job_payload as _replace_cron_job_payload_facade,
    save_compression_checkpoint as _save_compression_checkpoint_facade,
    save_seed_bundle as _save_seed_bundle_facade,
    set_cron_job_enabled as _set_cron_job_enabled_facade,
    storage_status as _storage_status_facade,
    trigger_cron_run as _trigger_cron_run_facade,
    update_derived_views as _update_derived_views_facade,
)
from openminion.modules.storage.migrations.metadata import (
    ensure_module_metadata_via_store,
)
from openminion.modules.artifact.refs import (
    add_reference_edges,
    create_default_artifactctl,
    normalize_artifact_ref_targets,
)
from openminion.modules.storage.backends.blob_store import BlobStoreFS
from openminion.modules.storage.backends.hybrid_store import HybridStore
from openminion.modules.storage.record_store import RecordStore, RecordStoreSQLite
from openminion.modules.storage.runtime.provider_selection import (
    resolve_storage_provider,
)

from openminion.base.time import utc_now_iso as _utc_now_iso


def _resolve_db_path(database_path: str | Path) -> Path:
    return Path(database_path).expanduser().resolve()


def _resolve_session_storage_roots(
    db_path: Path,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> tuple[Path, Path]:
    resolved_env = resolve_environment_config_with_explicit_env(env)
    standalone_mode = is_module_standalone_mode(resolved_env)
    if not standalone_mode:
        try:
            home_root = resolve_module_home_root(
                None,
                resolved_env,
                fallback_to_cwd=True,
            )
            data_root = resolve_module_data_root(
                home_root=home_root,
                env=resolved_env,
            )
        except Exception:  # noqa: BLE001
            data_root = None
        if data_root is not None:
            resolved_data_root = data_root.resolve()
            resolved_db_path = db_path.resolve()
            try:
                resolved_db_path.relative_to(resolved_data_root)
            except ValueError:
                pass
            else:
                return (resolved_data_root / "storage").resolve(), resolved_data_root
    return (db_path.parent / "storage").resolve(), db_path.parent.resolve()


def _stable_hash(value: Any) -> str:
    encoded = to_json(value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_MODULE_ID = module_id_from_package(__package__)


def _build_default_hybrid_store(
    *,
    record_store: RecordStore,
    database_path: Path,
    env: EnvironmentConfig | Mapping[str, Any] | None,
    is_memory: bool,
) -> tuple[HybridStore, Path | None]:
    temp_root: Path | None = None
    if is_memory:
        temp_root = Path(tempfile.mkdtemp(prefix="openminion-session-store-")).resolve()
        blob_root = temp_root / "storage"
        fallback_root = temp_root
    else:
        blob_root, fallback_root = _resolve_session_storage_roots(
            database_path,
            env=env,
        )
    blob_store = BlobStoreFS(blob_root)
    return (
        HybridStore(
            record_store=record_store,
            blob_store=blob_store,
            fallback_root=fallback_root,
            default_namespace="sessctl",
        ),
        temp_root,
    )


def _table_columns(record_store: RecordStore, table_name: str) -> set[str]:
    capabilities = record_store.capabilities()
    if bool(capabilities.get("raw_sql")):
        rows = record_store.query_dicts(f"PRAGMA table_info({table_name})")
        return {str(row["name"]) for row in rows}
    rows = record_store.query_dicts(
        """
        SELECT column_name AS name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = ?
        """,
        (table_name,),
    )
    return {str(row["name"]) for row in rows}


def _ensure_store_column(
    record_store: RecordStore,
    *,
    table_name: str,
    column_name: str,
    ddl_tail: str,
) -> None:
    columns = _table_columns(record_store, table_name)
    if not columns or column_name in columns:
        return
    record_store.execute_count(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl_tail}"
    )


def _resolve_session_storage_provider(
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> str:
    resolved_env = resolve_environment_config_with_explicit_env(env)
    return resolve_storage_provider(
        module="session",
        raw_provider=resolved_env.get("OPENMINION_SESSION_STORAGE_PROVIDER", ""),
        source_label="OPENMINION_SESSION_STORAGE_PROVIDER",
        error_factory=RuntimeError,
        unsupported_message_builder=(
            lambda provider, _supported, _source: (
                "Unsupported session storage provider "
                f"{provider!r}. Supported provider: sqlite."
            )
        ),
    )


@dataclass(frozen=True)
class SliceLimits:
    max_turns: int = 8
    max_tool_events: int = 12
    summary_variant: str = "auto"  # short|long|auto
    include_open_tasks: bool = True
    include_active_state: bool = True


_ARTIFACTCTL_UNSET = object()
_CLOSED_TASK_STATUSES = _SLICE_CLOSED_TASK_STATUSES
_LOG = logging.getLogger(__name__)


class SQLiteSessionStore(SessionStore):
    """SQLite-backed session store for openminion-session."""

    contract_version = SESSION_INTERFACE_VERSION

    def __init__(
        self,
        database_path: str | Path,
        *,
        record_store: RecordStore | None = None,
        hybrid_store: HybridStore | None = None,
        artifactctl: Any = _ARTIFACTCTL_UNSET,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ) -> None:
        is_memory = self._init_backend_path_and_env(
            database_path=database_path,
            env=env,
            artifactctl=artifactctl,
        )
        self._storage_provider = _resolve_session_storage_provider(env=self._env)
        raw_db_path = str(database_path).strip()
        self._record_store: RecordStore
        if record_store is None:
            self._record_store = RecordStoreSQLite(
                raw_db_path if is_memory else self._path, wal=True
            )
        else:
            self._record_store = record_store
        self._conn = getattr(self._record_store, "connection", None)
        self._configure_connection()
        self._verify_startup_integrity(is_memory=is_memory)
        self._init_hybrid_store_and_temp_root(
            hybrid_store=hybrid_store,
            is_memory=is_memory,
        )
        self._migrate()
        self._finalize_module_metadata()
        self._initialize_store_components()

    def _finalize_module_metadata(self) -> None:
        ensure_module_metadata_via_store(
            self._record_store,
            module_id="session",
            schema_head=list_migrations()[-1],
        )

    def _init_backend_path_and_env(
        self,
        *,
        database_path: str | Path,
        env: EnvironmentConfig | Mapping[str, Any] | None,
        artifactctl: Any,
    ) -> bool:
        self._env = resolve_environment_config_with_explicit_env(env)
        self._artifactctl = artifactctl
        raw_db_path = str(database_path).strip()
        is_memory = raw_db_path == ":memory:"
        self._path = Path(":memory:") if is_memory else _resolve_db_path(database_path)
        if not is_memory:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._slice_cache: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        return is_memory

    def _init_hybrid_store_and_temp_root(
        self,
        *,
        hybrid_store: HybridStore | None,
        is_memory: bool,
    ) -> None:
        if hybrid_store is None:
            self._hybrid_store, self._temp_root = _build_default_hybrid_store(
                record_store=self._record_store,
                database_path=self._path,
                env=self._env,
                is_memory=is_memory,
            )
        else:
            self._hybrid_store = hybrid_store
            self._temp_root = None

    def _initialize_store_components(self) -> None:
        components = build_store_components(
            record_store=self._record_store,
            lock=self._lock,
            slice_cache=self._slice_cache,
            get_session=self.get_session,
            append_event=self.append_event,
            touch_session_tx=self._touch_session_tx,
            invalidate_slice_cache=self._invalidate_slice_cache,
            add_artifact_refs=self._add_artifact_refs,
            latest_event_seq=self._latest_event_seq,
            normalize_limits=self._normalize_limits,
            stable_hash=_stable_hash,
        )
        self._event_store = components.event_store
        self._slice_queries = components.slice_queries
        self._event_writer = components.event_writer
        self._cron_store = components.cron_store
        self._state_store = components.state_store
        self._summary_store = components.summary_store
        self._context_store = components.context_store
        self._run_store = components.run_store
        self._session_helper = components.session_helper
        self._replay_helper = components.replay_helper
        self._slice_store = components.slice_store

    @property
    def run_store(self) -> RunStore:
        return self._run_store

    create_snapshot = _create_snapshot_facade
    add_cron_job = _add_cron_job_facade
    get_cron_job = _get_cron_job_facade
    list_cron_jobs = _list_cron_jobs_facade
    set_cron_job_enabled = _set_cron_job_enabled_facade
    replace_cron_job_payload = _replace_cron_job_payload_facade
    delete_cron_job = _delete_cron_job_facade
    trigger_cron_run = _trigger_cron_run_facade
    list_cron_runs = _list_cron_runs_facade
    enqueue_due_cron_runs = _enqueue_due_cron_runs_facade
    acquire_cron_runs = _acquire_cron_runs_facade
    renew_cron_run_lease = _renew_cron_run_lease_facade
    finish_cron_run = _finish_cron_run_facade
    delete_old_cron_runs = _delete_old_cron_runs_facade
    mark_cron_delivery_target = _mark_cron_delivery_target_facade
    storage_status = _storage_status_facade
    reindex_sidecars = _reindex_sidecars_facade
    create_prompt_context = _create_prompt_context_facade
    close_prompt_context = _close_prompt_context_facade
    get_active_prompt_context = _get_active_prompt_context_facade
    save_compression_checkpoint = _save_compression_checkpoint_facade
    get_latest_checkpoint = _get_latest_checkpoint_facade
    save_seed_bundle = _save_seed_bundle_facade
    get_latest_seed_bundle = _get_latest_seed_bundle_facade
    create_run_record = _create_run_record_facade
    finish_run_record = _finish_run_record_facade
    add_run_usage_delta = _add_run_usage_delta_facade
    get_run_record = _get_run_record_facade
    list_run_records = _list_run_records_facade
    add_message_ref = _add_message_ref_facade
    update_derived_views = _update_derived_views_facade
    get_slice = _get_slice_facade
    _list_recent_archive_ref_lines = _list_recent_archive_ref_lines_facade
    enforce_context_manifest = _enforce_context_manifest_facade
    emit_canonical_event = _emit_canonical_event_facade
    get_replay_events = _get_replay_events_facade
    get_resume_state = _get_resume_state_facade

    def _resolve_artifactctl(self) -> Any | None:
        if self._artifactctl is _ARTIFACTCTL_UNSET:
            self._artifactctl = create_default_artifactctl()
        return self._artifactctl

    def _add_artifact_refs(self, *, session_id: str, ref_values: Any) -> None:
        targets = normalize_artifact_ref_targets(ref_values)
        if not targets:
            return
        add_reference_edges(
            artifactctl=self._resolve_artifactctl(),
            owner_type="session",
            owner_id=session_id,
            ref_values=targets,
        )

    @property
    def database_path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            close_fn = getattr(self._record_store, "close", None)
            if callable(close_fn):
                close_fn()

    def _configure_connection(self) -> None:
        if self._conn is None:
            raise RuntimeError("record_store must expose sqlite connection")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")

    def _verify_startup_integrity(self, *, is_memory: bool) -> None:
        if is_memory:
            return
        try:
            report = verify_module_integrity(_MODULE_ID, self._path)
        except Exception as exc:
            _LOG.error(
                "session store startup integrity check failed: %s",
                exc,
                exc_info=True,
            )
            raise RuntimeError("Session store startup integrity check failed") from exc
        if not bool(report.get("ok", False)):
            _LOG.error("session store startup integrity violation: %s", report)
            raise RuntimeError("Session store startup integrity violation")

    def _migrate(self) -> None:
        with self._lock:
            self._record_store.execute_count(
                """
                CREATE TABLE IF NOT EXISTS migrations (
                  version     INTEGER PRIMARY KEY,
                  name        TEXT NOT NULL,
                  applied_at  TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row["version"])
                for row in self._record_store.query_dicts(
                    "SELECT version FROM migrations"
                )
            }
            for migration in MIGRATIONS:
                if migration.version in applied:
                    continue
                with self._record_store.transaction():
                    for statement in migration.statements:
                        self._record_store.execute_count(statement)
                    self._record_store.execute_count(
                        "INSERT INTO migrations(version, name, applied_at) VALUES (?, ?, ?)",
                        (migration.version, migration.name, _utc_now_iso()),
                    )

            self._ensure_column("sessions", "active_profile_version", "TEXT")
            self._ensure_column("session_events", "prompt_context_id", "TEXT")

    def _ensure_column(self, table: str, column: str, ddl_tail: str) -> None:
        _ensure_store_column(
            self._record_store,
            table_name=table,
            column_name=column,
            ddl_tail=ddl_tail,
        )

    def _bootstrap_record_store_schema(self) -> None:
        with self._lock:
            for migration in MIGRATIONS:
                for statement in migration.statements:
                    self._record_store.execute_count(statement)
            _ensure_store_column(
                self._record_store,
                table_name="sessions",
                column_name="active_profile_version",
                ddl_tail="TEXT",
            )

    def create_session(
        self,
        *,
        initial_agent_id: str | None = None,
        profile_version: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
        status: str = "active",
        session_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        return self._session_helper.create_session(
            initial_agent_id=initial_agent_id,
            profile_version=profile_version,
            title=title,
            tags=tags,
            status=status,
            session_id=session_id,
            meta=meta,
        )

    def list_sessions(
        self,
        *,
        filters: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._session_helper.list_sessions(filters=filters, limit=limit)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self._session_helper.get_session(session_id)

    def set_status(self, session_id: str, status: str) -> None:
        self._session_helper.set_status(session_id, status)

    def update_session_status(self, session_id: str, status: str) -> None:
        self._session_helper.update_session_status(session_id, status)

    def bind_agent(
        self,
        session_id: str,
        agent_id: str,
        profile_version: str,
        *,
        render_version: str | None = None,
        reason: str | None = None,
    ) -> None:
        self._session_helper.bind_agent(
            session_id,
            agent_id,
            profile_version,
            render_version=render_version,
            reason=reason,
        )

    def append_llm_request_started(
        self,
        session_id: str,
        *,
        purpose: str,
        profile_version: str,
        render_version: str,
        agent_id: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        parent_event_id: str | None = None,
    ) -> str:
        payload = {
            "purpose": purpose,
            "profile_version": profile_version,
            "render_version": render_version,
        }
        trace_payload: dict[str, str] = {}
        if trace_id:
            trace_payload["trace_id"] = trace_id
        if task_id:
            trace_payload["task_id"] = task_id
        return self.append_event(
            session_id,
            event_type="llm.request.started",
            payload=payload,
            actor_type="agent" if agent_id else "system",
            actor_id=agent_id,
            trace=trace_payload or None,
            parent_event_id=parent_event_id,
            importance=1,
            status="started",
        )

    def archive_session(self, session_id: str) -> None:
        self._session_helper.archive_session(session_id)

    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        attachments: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        return self._event_writer.append_turn(
            session_id,
            role,
            content,
            attachments=attachments,
            meta=meta,
        )

    def list_turns(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        before_ts: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._event_store.list_turns(
            session_id,
            lock=self._lock,
            limit=limit,
            before_ts=before_ts,
        )

    def get_recent_turns(
        self, session_id: str, limit_messages: int
    ) -> list[dict[str, Any]]:
        return self._event_store.get_recent_turns(session_id, limit_messages)

    def append_event(
        self,
        session_id: str,
        type: str | None = None,
        payload: dict[str, Any] | None = None,
        *,
        event_type: str | None = None,
        actor_type: str = "system",
        actor_id: str | None = None,
        trace: dict[str, Any] | None = None,
        refs: dict[str, Any] | None = None,
        parent_event_id: str | None = None,
        importance: int = 1,
        redaction: str | None = None,
        agent_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        task_id: str | None = None,
        parent_id: str | None = None,
        artifact_refs: list[str] | None = None,
        memory_refs: list[str] | None = None,
        status: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> str:
        return self._event_writer.append_event(
            session_id,
            type,
            payload,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            trace=trace,
            refs=refs,
            parent_event_id=parent_event_id,
            importance=importance,
            redaction=redaction,
            agent_id=agent_id,
            trace_id=trace_id,
            span_id=span_id,
            task_id=task_id,
            parent_id=parent_id,
            artifact_refs=artifact_refs,
            memory_refs=memory_refs,
            status=status,
            error=error,
        )

    def list_events(
        self,
        session_id: str,
        *,
        event_type: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self._event_store.list_events_compat(
            session_id,
            lock=self._lock,
            event_type=event_type,
            trace_id=trace_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
        )

    def get_events(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self._event_store.get_events(
            session_id,
            after_seq=after_seq,
            types=types,
            limit=limit,
        )

    def get_event_by_id(self, event_id: str) -> dict[str, Any] | None:
        return self._event_store.get_event_by_id(event_id)

    def get_events_by_parent_and_type(
        self,
        parent_event_id: str,
        event_type: str,
    ) -> list[dict[str, Any]]:
        return self._event_store.get_events_by_parent_and_type(
            parent_event_id,
            event_type,
        )

    def get_total_turn_count(self, session_id: str) -> int:
        return self._event_store.get_total_turn_count(session_id)

    def get_active_task_plan(self, session_id: str) -> dict[str, Any] | None:
        return self._event_store.get_active_task_plan(session_id)

    def latest_event_seq(self, session_id: str) -> int:
        return self._latest_event_seq(session_id)

    def get_recent_tool_events(
        self, session_id: str, limit: int
    ) -> list[dict[str, Any]]:
        return self._event_store.get_recent_tool_events(session_id, limit)

    def put_working_state(
        self,
        session_id: str,
        *,
        state_ref: str | None = None,
        state_inline: dict[str, Any] | None = None,
    ) -> int:
        return self._state_store.put_working_state(
            session_id,
            state_ref=state_ref,
            state_inline=state_inline,
        )

    def get_latest_working_state(self, session_id: str) -> dict[str, Any] | None:
        return self._state_store.get_latest_working_state(session_id)

    def get_active_state(self, session_id: str) -> dict[str, Any]:
        return self._state_store.get_active_state(session_id)

    def set_summary_base(self, session_id: str, base_ref: str) -> None:
        self._summary_store.set_summary_base(session_id, base_ref)

    def append_summary_delta(self, session_id: str, delta_ref: str) -> None:
        self._summary_store.append_summary_delta(session_id, delta_ref)

    def get_summaries(self, session_id: str) -> dict[str, Any]:
        return self._summary_store.get_summaries(session_id)

    def get_summary(self, session_id: str, *, variant: str = "short") -> str:
        return self._summary_store.get_summary(session_id, variant=variant)

    def needs_summary_update(
        self, session_id: str, *, threshold_events: int = 40
    ) -> bool:
        return self._summary_store.needs_summary_update(
            session_id, threshold_events=threshold_events
        )

    def update_summary(
        self,
        session_id: str,
        summary_short: str,
        *,
        summary_long: str | None = None,
        based_on_seq: int,
    ) -> None:
        self._summary_store.update_summary(
            session_id,
            summary_short,
            summary_long=summary_long,
            based_on_seq=based_on_seq,
        )

    def _normalize_limits(
        self, limits: SliceLimits | Mapping[str, Any]
    ) -> dict[str, Any]:
        if isinstance(limits, SliceLimits):
            source: Mapping[str, Any] = {
                "max_turns": limits.max_turns,
                "max_tool_events": limits.max_tool_events,
                "summary_variant": limits.summary_variant,
                "include_open_tasks": limits.include_open_tasks,
                "include_active_state": limits.include_active_state,
            }
        else:
            source = limits
        summary_variant = str(source.get("summary_variant", "auto")).lower().strip()
        if summary_variant not in {"short", "long", "auto"}:
            summary_variant = "auto"
        return {
            "max_turns": max(1, int(source.get("max_turns", 8))),
            "max_tool_events": max(1, int(source.get("max_tool_events", 12))),
            "archive_ref_limit": max(1, int(source.get("archive_ref_limit", 3))),
            "summary_variant": summary_variant,
            "include_open_tasks": bool(source.get("include_open_tasks", True)),
            "include_active_state": bool(source.get("include_active_state", True)),
        }

    def _derive_open_tasks(
        self, *, session_id: str, upto_seq: int | None = None
    ) -> list[dict[str, Any]]:
        return self._slice_queries.derive_open_tasks(
            session_id=session_id,
            upto_seq=upto_seq,
        )

    def _latest_event_seq_tx(self, session_id: str) -> int:
        return self._slice_queries.latest_event_seq_tx(session_id)

    def _touch_session_tx(self, *, session_id: str, ts: str) -> None:
        self._record_store.execute_count(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (ts, session_id),
        )

    def _invalidate_slice_cache(self, session_id: str) -> None:
        stale = [key for key in self._slice_cache if key[0] == session_id]
        for key in stale:
            self._slice_cache.pop(key, None)

    def _row_to_session_event(self, row: sqlite3.Row) -> dict[str, Any]:
        return row_to_session_event(row)

    def _latest_event_seq(self, session_id: str) -> int:
        with self._lock:
            return self._latest_event_seq_tx(session_id)

    def backfill_events(
        self,
        session_id: str,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._replay_helper.backfill_events(session_id, events)


class PostgresSessionStore(SQLiteSessionStore):
    """Postgres-backed session store using the shared RecordStore seam."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        record_store: RecordStore,
        hybrid_store: HybridStore | None = None,
        artifactctl: Any = _ARTIFACTCTL_UNSET,
        env: EnvironmentConfig | Mapping[str, Any] | None = None,
    ) -> None:
        is_memory = self._init_backend_path_and_env(
            database_path=database_path,
            env=env,
            artifactctl=artifactctl,
        )
        self._storage_provider = "postgres"
        self._record_store: RecordStore
        self._record_store = record_store
        self._conn = None
        self._init_hybrid_store_and_temp_root(
            hybrid_store=hybrid_store,
            is_memory=is_memory,
        )
        self._bootstrap_record_store_schema()
        self._finalize_module_metadata()
        self._initialize_store_components()
