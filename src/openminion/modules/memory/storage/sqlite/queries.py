import re
import sqlite3

MIGRATION_V1 = """
CREATE TABLE IF NOT EXISTS memory_records (
  id                 TEXT PRIMARY KEY,
  scope              TEXT NOT NULL,
  namespace_json     TEXT,
  type               TEXT NOT NULL,
  key                TEXT,
  title              TEXT,
  content_json       TEXT NOT NULL,
  tags_json          TEXT NOT NULL DEFAULT '[]',
  entities_json      TEXT NOT NULL DEFAULT '[]',
  goal_id            TEXT,
  source             TEXT NOT NULL,
  confidence         REAL NOT NULL,
  evidence_json      TEXT NOT NULL DEFAULT '[]',
  meta_json          TEXT NOT NULL DEFAULT '{}',
  last_hit_at        TEXT,
  event_time         TEXT,
  valid_to           TEXT,
  tier               TEXT NOT NULL DEFAULT 'working',
  access_count       INTEGER NOT NULL DEFAULT 0,
  expires_at         TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  supersedes_id      TEXT,
  superseded_by_id   TEXT,
  supersession_reason TEXT,
  is_deleted         INTEGER NOT NULL DEFAULT 0,

  FOREIGN KEY(supersedes_id) REFERENCES memory_records(id),
  FOREIGN KEY(superseded_by_id) REFERENCES memory_records(id)
);

CREATE TABLE IF NOT EXISTS memory_candidates (
  candidate_id       TEXT PRIMARY KEY,
  session_id         TEXT NOT NULL,
  proposed_scope     TEXT NOT NULL,
  type               TEXT NOT NULL,
  key                TEXT,
  title              TEXT,
  content_json       TEXT NOT NULL,
  tags_json          TEXT NOT NULL DEFAULT '[]',
  entities_json      TEXT NOT NULL DEFAULT '[]',
  source             TEXT NOT NULL,
  confidence         REAL NOT NULL,
  evidence_json      TEXT NOT NULL DEFAULT '[]',
  meta_json          TEXT NOT NULL DEFAULT '{}',
  status             TEXT NOT NULL,
  review_json        TEXT,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_tier_transitions (
  transition_id       TEXT PRIMARY KEY,
  record_id           TEXT NOT NULL,
  scope               TEXT NOT NULL,
  record_type         TEXT NOT NULL,
  from_tier           TEXT NOT NULL,
  to_tier             TEXT NOT NULL,
  transition_reason   TEXT NOT NULL,
  transition_at       TEXT NOT NULL,
  access_count        INTEGER NOT NULL DEFAULT 0,
  meta_json           TEXT NOT NULL DEFAULT '{}',

  FOREIGN KEY(record_id) REFERENCES memory_records(id)
);

CREATE TABLE IF NOT EXISTS memory_entities (
  entity             TEXT NOT NULL,
  record_id          TEXT NOT NULL,
  scope              TEXT NOT NULL,
  type               TEXT NOT NULL,
  created_at         TEXT NOT NULL,

  PRIMARY KEY (entity, record_id),
  FOREIGN KEY(record_id) REFERENCES memory_records(id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  id UNINDEXED,
  scope,
  type,
  key,
  title,
  content_text,
  tags_text,
  entities_text
);

CREATE INDEX IF NOT EXISTS idx_records_scope_type_updated
  ON memory_records(scope, type, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_records_scope_key
  ON memory_records(scope, type, key);

CREATE INDEX IF NOT EXISTS idx_records_confidence
  ON memory_records(confidence DESC);

CREATE INDEX IF NOT EXISTS idx_records_scope_tier_updated
  ON memory_records(scope, tier, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_records_goal_id_updated
  ON memory_records(goal_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_candidates_session_status
  ON memory_candidates(session_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_candidates_scope_status
  ON memory_candidates(proposed_scope, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_entities_entity
  ON memory_entities(entity);

CREATE INDEX IF NOT EXISTS idx_entities_scope_entity
  ON memory_entities(scope, entity);

CREATE INDEX IF NOT EXISTS idx_memory_tier_transitions_record
  ON memory_tier_transitions(record_id, transition_at DESC);

CREATE INDEX IF NOT EXISTS idx_memory_tier_transitions_scope
  ON memory_tier_transitions(scope, transition_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_records_scope_type_key_active
  ON memory_records(scope, type, key)
  WHERE key IS NOT NULL AND is_deleted = 0 AND superseded_by_id IS NULL;
"""

CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS migrations (
  version     INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  applied_at  TEXT NOT NULL
);
"""

GET_APPLIED_MIGRATIONS = "SELECT version FROM migrations ORDER BY version"

RECORD_MIGRATION = "INSERT INTO migrations (version, name, applied_at) VALUES (?, ?, ?)"

CREATE_MEMORY_RELATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS memory_relations (
    relation_id TEXT PRIMARY KEY,
    source_record_id TEXT NOT NULL,
    target_record_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
)
"""

CREATE_MEMORY_RELATIONS_SOURCE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_memory_relations_source
ON memory_relations(source_record_id, created_at DESC)
"""

CREATE_MEMORY_RELATIONS_TARGET_INDEX = """
CREATE INDEX IF NOT EXISTS idx_memory_relations_target
ON memory_relations(target_record_id, created_at DESC)
"""

_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def sanitize_fts_query(raw_query: str) -> str:
    """Convert punctuation-heavy freeform text into a safe FTS token query."""
    tokens = _FTS_TOKEN_RE.findall(str(raw_query or ""))
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens[:16])


def is_fts_query_parse_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "no such column" in message or "fts5" in message or "malformed" in message
