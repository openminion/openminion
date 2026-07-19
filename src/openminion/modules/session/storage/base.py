from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


class SessionStore(ABC):
    @abstractmethod
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
    ) -> str: ...

    @abstractmethod
    def list_sessions(
        self,
        *,
        filters: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_session(self, session_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def set_status(self, session_id: str, status: str) -> None: ...

    @abstractmethod
    def update_session_status(self, session_id: str, status: str) -> None: ...

    @abstractmethod
    def bind_agent(
        self,
        session_id: str,
        agent_id: str,
        profile_version: str,
        *,
        render_version: str | None = None,
        reason: str | None = None,
    ) -> None: ...

    @abstractmethod
    def archive_session(self, session_id: str) -> None: ...

    @abstractmethod
    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        attachments: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str: ...

    @abstractmethod
    def list_turns(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        before_ts: str | None = None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_recent_turns(
        self, session_id: str, limit_messages: int
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
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
    ) -> str: ...

    @abstractmethod
    def list_events(
        self,
        session_id: str,
        *,
        event_type: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_events(
        self,
        session_id: str,
        *,
        after_seq: int | None = None,
        types: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_recent_tool_events(
        self, session_id: str, limit: int
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def put_working_state(
        self,
        session_id: str,
        *,
        state_ref: str | None = None,
        state_inline: dict[str, Any] | None = None,
    ) -> int: ...

    @abstractmethod
    def get_latest_working_state(self, session_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_active_state(self, session_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def set_summary_base(self, session_id: str, base_ref: str) -> None: ...

    @abstractmethod
    def append_summary_delta(self, session_id: str, delta_ref: str) -> None: ...

    @abstractmethod
    def get_summaries(self, session_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def get_summary(self, session_id: str, *, variant: str = "short") -> str: ...

    @abstractmethod
    def needs_summary_update(
        self, session_id: str, *, threshold_events: int = 40
    ) -> bool: ...

    @abstractmethod
    def update_summary(
        self,
        session_id: str,
        summary_short: str,
        *,
        summary_long: str | None = None,
        based_on_seq: int,
    ) -> None: ...

    @abstractmethod
    def create_snapshot(self, session_id: str, seq_upto: int | None = None) -> str: ...

    @abstractmethod
    def update_derived_views(self, session_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def get_slice(
        self,
        session_id: str,
        purpose: str,
        limits: Any | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def add_cron_job(
        self,
        *,
        name: str,
        schedule: Mapping[str, Any],
        payload: Mapping[str, Any],
        description: str | None = None,
        enabled: bool = True,
        agent_id: str | None = None,
        session_target: str | None = None,
        wake_mode: str | None = None,
        delivery: Mapping[str, Any] | None = None,
        delete_after_run: bool | None = None,
        misfire_policy: str | Mapping[str, Any] | None = None,
        max_lateness_s: int = 600,
        max_concurrency: int = 1,
        job_id: str | None = None,
    ) -> str: ...

    @abstractmethod
    def get_cron_job(self, job_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def list_cron_jobs(
        self, *, enabled: bool | None = None, limit: int = 50
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def set_cron_job_enabled(self, job_id: str, enabled: bool) -> None: ...

    @abstractmethod
    def replace_cron_job_payload(
        self, job_id: str, payload: Mapping[str, Any]
    ) -> None: ...

    @abstractmethod
    def delete_cron_job(self, job_id: str) -> None: ...

    @abstractmethod
    def trigger_cron_run(
        self,
        job_id: str,
        *,
        due_at: str | None = None,
        lease_owner: str | None = None,
        lease_ttl_s: int = 60,
    ) -> str: ...

    @abstractmethod
    def list_cron_runs(
        self,
        *,
        job_id: str | None = None,
        limit: int = 100,
        states: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def enqueue_due_cron_runs(
        self,
        daemon_id: str,
        *,
        lease_ttl_s: int = 60,
        max_jobs: int = 50,
        now_iso: str | None = None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def acquire_cron_runs(
        self,
        daemon_id: str,
        *,
        lease_ttl_s: int = 60,
        limit: int = 10,
        now_iso: str | None = None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def renew_cron_run_lease(
        self,
        run_id: str,
        *,
        daemon_id: str,
        lease_ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> bool: ...

    @abstractmethod
    def acquire_session_turn_lease(
        self,
        session_id: str,
        *,
        owner: str,
        request_id: str,
        ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> Any: ...

    @abstractmethod
    def renew_session_turn_lease(
        self,
        session_id: str,
        *,
        owner: str,
        fence_token: int,
        ttl_s: int = 60,
        now_iso: str | None = None,
    ) -> bool: ...

    @abstractmethod
    def release_session_turn_lease(
        self,
        session_id: str,
        *,
        owner: str,
        fence_token: int,
        now_iso: str | None = None,
    ) -> bool: ...

    @abstractmethod
    def assert_session_turn_fence(
        self,
        session_id: str,
        *,
        fence_token: int,
    ) -> None: ...

    @abstractmethod
    def finish_cron_run(
        self,
        run_id: str,
        *,
        state: str,
        summary: str | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
        error: dict[str, Any] | None = None,
        isolated_session_id: str | None = None,
        now_iso: str | None = None,
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    def delete_old_cron_runs(self, before_iso: str) -> int: ...

    @abstractmethod
    def mark_cron_delivery_target(self, run_id: str, *, target: str) -> bool: ...

    @abstractmethod
    def create_prompt_context(
        self,
        session_id: str,
        *,
        seed_bundle_id: str | None = None,
        checkpoint_id: str | None = None,
        prefix_hash: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str: ...

    @abstractmethod
    def close_prompt_context(
        self,
        prompt_context_id: str,
        *,
        rollover_reason: str | None = None,
    ) -> None: ...

    @abstractmethod
    def get_active_prompt_context(self, session_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def save_compression_checkpoint(
        self,
        session_id: str,
        bundle_json: str,
        *,
        up_to_event_id: str | None = None,
        reason: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str: ...

    @abstractmethod
    def get_latest_checkpoint(self, session_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def save_seed_bundle(
        self,
        session_id: str,
        source_bundle_id: str,
        sections_json: str,
        total_tokens: int,
        *,
        source_checkpoint_id: str | None = None,
        budgets_json: str = "{}",
        up_to_event_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str: ...

    @abstractmethod
    def get_latest_seed_bundle(self, session_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def create_run_record(
        self,
        session_id: str,
        run_type: str = "llm",
        *,
        run_id: str | None = None,
        prompt_context_id: str | None = None,
        model_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str: ...

    @abstractmethod
    def finish_run_record(
        self,
        run_id: str,
        *,
        status: str = "completed",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None: ...

    @abstractmethod
    def add_run_usage_delta(
        self,
        run_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None: ...

    @abstractmethod
    def get_run_record(self, run_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def list_run_records(self, session_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def add_message_ref(
        self,
        session_id: str,
        role: str,
        *,
        run_id: str | None = None,
        event_id: str | None = None,
        content_ref: str | None = None,
        content_inline: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str: ...

    @abstractmethod
    def get_replay_events(
        self,
        session_id: str,
        *,
        from_seq: int = 0,
        to_seq: int | None = None,
        event_types: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_resume_state(self, session_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def backfill_events(
        self,
        session_id: str,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    @abstractmethod
    def close(self) -> None: ...
