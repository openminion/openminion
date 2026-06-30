BOOTSTRAP_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS sessions (
      session_id           TEXT PRIMARY KEY,
      created_at           TEXT NOT NULL,
      updated_at           TEXT NOT NULL,
      title                TEXT,
      status               TEXT NOT NULL,
      active_agent_id      TEXT,
      participants_json    TEXT NOT NULL DEFAULT '[]',
      root_goal            TEXT,
      tags_json            TEXT NOT NULL DEFAULT '[]',
      config_snapshot_ref  TEXT,
      meta_json            TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_status_updated
    ON sessions(status, updated_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS turns (
      turn_id           TEXT PRIMARY KEY,
      session_id        TEXT NOT NULL,
      ts                TEXT NOT NULL,
      role              TEXT NOT NULL,
      content           TEXT NOT NULL,
      attachments_json  TEXT NOT NULL DEFAULT '[]',
      meta_json         TEXT NOT NULL DEFAULT '{}',
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_turns_session_ts
    ON turns(session_id, ts, turn_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
      event_id            TEXT PRIMARY KEY,
      session_id          TEXT NOT NULL,
      ts                  TEXT NOT NULL,
      type                TEXT NOT NULL,
      agent_id            TEXT,
      trace_id            TEXT,
      task_id             TEXT,
      parent_id           TEXT,
      payload_json        TEXT NOT NULL DEFAULT '{}',
      artifact_refs_json  TEXT NOT NULL DEFAULT '[]',
      memory_refs_json    TEXT NOT NULL DEFAULT '[]',
      status              TEXT,
      error_json          TEXT,
      FOREIGN KEY(session_id) REFERENCES sessions(session_id),
      FOREIGN KEY(parent_id) REFERENCES events(event_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_session_ts
    ON events(session_id, ts, event_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_trace
    ON events(trace_id, ts)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_type
    ON events(type, ts)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_status
    ON events(status, ts)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_agent
    ON events(agent_id, ts)
    """,
    """
    CREATE TABLE IF NOT EXISTS working_state (
      session_id        TEXT NOT NULL,
      version           INTEGER NOT NULL,
      ts                TEXT NOT NULL,
      state_ref         TEXT,
      state_inline_json TEXT,
      PRIMARY KEY(session_id, version),
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_working_state_latest
    ON working_state(session_id, version DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS summaries (
      session_id         TEXT PRIMARY KEY,
      base_ref           TEXT,
      updated_at         TEXT NOT NULL,
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS summary_deltas (
      session_id         TEXT NOT NULL,
      seq                INTEGER NOT NULL,
      delta_ref          TEXT NOT NULL,
      ts                 TEXT NOT NULL,
      PRIMARY KEY(session_id, seq),
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_summary_deltas_session_ts
    ON summary_deltas(session_id, ts, seq)
    """,
)

EVENT_SOURCED_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS session_events (
      event_id          TEXT PRIMARY KEY,
      session_id        TEXT NOT NULL,
      seq               INTEGER NOT NULL,
      timestamp         TEXT NOT NULL,
      event_type        TEXT NOT NULL,
      actor_type        TEXT NOT NULL,
      actor_id          TEXT,
      trace_id          TEXT,
      span_id           TEXT,
      task_id           TEXT,
      parent_event_id   TEXT,
      payload_json      TEXT NOT NULL DEFAULT '{}',
      refs_json         TEXT,
      importance        INTEGER NOT NULL DEFAULT 1,
      redaction         TEXT NOT NULL DEFAULT 'none',
      FOREIGN KEY(session_id) REFERENCES sessions(session_id),
      FOREIGN KEY(parent_event_id) REFERENCES session_events(event_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_session_events_session_seq
    ON session_events(session_id, seq)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_events_session_type_seq
    ON session_events(session_id, event_type, seq)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_events_trace
    ON session_events(trace_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_events_task
    ON session_events(task_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS session_snapshots (
      snapshot_id       TEXT PRIMARY KEY,
      session_id        TEXT NOT NULL,
      seq_upto          INTEGER NOT NULL,
      summary_short     TEXT NOT NULL,
      summary_long      TEXT,
      state_json        TEXT NOT NULL DEFAULT '{}',
      open_tasks_json   TEXT NOT NULL DEFAULT '[]',
      created_at        TEXT NOT NULL,
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_snapshots_session_seq
    ON session_snapshots(session_id, seq_upto)
    """,
    """
    CREATE TABLE IF NOT EXISTS session_summaries (
      session_id        TEXT PRIMARY KEY,
      summary_short     TEXT NOT NULL,
      summary_long      TEXT,
      updated_at        TEXT NOT NULL,
      based_on_seq      INTEGER NOT NULL,
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """,
)

CRON_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS cron_jobs (
      job_id             TEXT PRIMARY KEY,
      name               TEXT NOT NULL,
      description        TEXT,
      enabled            INTEGER NOT NULL DEFAULT 1,
      agent_id           TEXT,
      schedule_json      TEXT NOT NULL,
      payload_json       TEXT NOT NULL,
      delivery_json      TEXT NOT NULL DEFAULT '{"mode":"none"}',
      session_target     TEXT NOT NULL,
      wake_mode          TEXT NOT NULL DEFAULT 'now',
      delete_after_run   INTEGER NOT NULL DEFAULT 0,
      misfire_policy     TEXT NOT NULL DEFAULT 'run_once',
      max_lateness_s     INTEGER NOT NULL DEFAULT 600,
      max_concurrency    INTEGER NOT NULL DEFAULT 1,
      next_due_at        TEXT,
      last_run_at        TEXT,
      created_at         TEXT NOT NULL,
      updated_at         TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_cron_jobs_enabled_due
    ON cron_jobs(enabled, next_due_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS cron_runs (
      run_id               TEXT PRIMARY KEY,
      job_id               TEXT,
      state                TEXT NOT NULL,
      due_at               TEXT NOT NULL,
      started_at           TEXT,
      finished_at          TEXT,
      isolated_session_id  TEXT,
      summary              TEXT,
      artifact_refs_json   TEXT NOT NULL DEFAULT '[]',
      error_json           TEXT,
      lease_owner          TEXT,
      lease_expires_at     TEXT,
      delivery_targets_json TEXT NOT NULL DEFAULT '[]',
      attempts             INTEGER NOT NULL DEFAULT 0,
      created_at           TEXT NOT NULL,
      updated_at           TEXT NOT NULL,
      FOREIGN KEY(job_id) REFERENCES cron_jobs(job_id) ON DELETE SET NULL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_cron_runs_job_due
    ON cron_runs(job_id, due_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_cron_runs_job_created
    ON cron_runs(job_id, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_cron_runs_state_lease
    ON cron_runs(state, lease_expires_at)
    """,
)

V15_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS prompt_contexts (
      prompt_context_id    TEXT PRIMARY KEY,
      session_id           TEXT NOT NULL,
      created_at           TEXT NOT NULL,
      closed_at            TEXT,
      status               TEXT NOT NULL DEFAULT 'active',
      seed_bundle_id       TEXT,
      checkpoint_id        TEXT,
      prefix_hash          TEXT,
      rollover_reason      TEXT,
      meta_json            TEXT NOT NULL DEFAULT '{}',
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_prompt_contexts_session_status
    ON prompt_contexts(session_id, status, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS compression_checkpoints (
      checkpoint_id        TEXT PRIMARY KEY,
      session_id           TEXT NOT NULL,
      bundle_json          TEXT NOT NULL,
      up_to_event_id       TEXT,
      created_at           TEXT NOT NULL,
      reason               TEXT,
      meta_json            TEXT NOT NULL DEFAULT '{}',
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_compression_checkpoints_session
    ON compression_checkpoints(session_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS seed_bundles (
      seed_id              TEXT PRIMARY KEY,
      session_id           TEXT NOT NULL,
      source_bundle_id     TEXT NOT NULL,
      source_checkpoint_id TEXT,
      sections_json        TEXT NOT NULL DEFAULT '[]',
      total_tokens         INTEGER NOT NULL DEFAULT 0,
      budgets_json         TEXT NOT NULL DEFAULT '{}',
      up_to_event_id       TEXT,
      created_at           TEXT NOT NULL,
      meta_json            TEXT NOT NULL DEFAULT '{}',
      FOREIGN KEY(session_id) REFERENCES sessions(session_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_seed_bundles_session
    ON seed_bundles(session_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS run_records (
      run_id               TEXT PRIMARY KEY,
      session_id           TEXT NOT NULL,
      prompt_context_id    TEXT,
      run_type             TEXT NOT NULL DEFAULT 'llm',
      status               TEXT NOT NULL DEFAULT 'pending',
      started_at           TEXT,
      finished_at          TEXT,
      input_tokens         INTEGER,
      output_tokens        INTEGER,
      model_id             TEXT,
      meta_json            TEXT NOT NULL DEFAULT '{}',
      FOREIGN KEY(session_id) REFERENCES sessions(session_id),
      FOREIGN KEY(prompt_context_id) REFERENCES prompt_contexts(prompt_context_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_run_records_session_status
    ON run_records(session_id, status, started_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS message_refs (
      ref_id               TEXT PRIMARY KEY,
      session_id           TEXT NOT NULL,
      run_id               TEXT,
      event_id             TEXT,
      role                 TEXT NOT NULL,
      content_ref          TEXT,
      content_inline       TEXT,
      seq                  INTEGER NOT NULL DEFAULT 0,
      created_at           TEXT NOT NULL,
      meta_json            TEXT NOT NULL DEFAULT '{}',
      FOREIGN KEY(session_id) REFERENCES sessions(session_id),
      FOREIGN KEY(run_id) REFERENCES run_records(run_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_message_refs_session_seq
    ON message_refs(session_id, seq)
    """,
)
