import json
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class CheckpointRecord:
    key: str
    payload: dict[str, Any]
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


class CheckpointStore(Protocol):
    """Put/get/list/delete contract for turn-state persistence.

    Implementations must be safe for use from multiple threads but are NOT
    required to be safe across processes (use the Postgres/Redis backends
    when you need that).
    """

    def put(self, record: CheckpointRecord) -> None: ...

    def get(self, key: str) -> CheckpointRecord | None: ...

    def list(self, *, prefix: str = "") -> list[str]: ...

    def delete(self, key: str) -> bool: ...


class InMemoryCheckpointStore:
    """Reference implementation. Thread-safe, process-local."""

    def __init__(self) -> None:
        self._store: dict[str, CheckpointRecord] = {}
        self._lock = threading.RLock()

    def put(self, record: CheckpointRecord) -> None:
        with self._lock:
            self._store[record.key] = record

    def get(self, key: str) -> CheckpointRecord | None:
        with self._lock:
            return self._store.get(key)

    def list(self, *, prefix: str = "") -> list[str]:
        with self._lock:
            if not prefix:
                return sorted(self._store.keys())
            return sorted(k for k in self._store if k.startswith(prefix))

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None


class PostgresCheckpointStore:
    _DDL = """
    CREATE TABLE IF NOT EXISTS openminion_checkpoints (
        key TEXT PRIMARY KEY,
        version INTEGER NOT NULL,
        payload JSONB NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """

    def __init__(self, *, dsn: str) -> None:
        self.dsn = dsn
        self._engine = None

    def _connect(self):
        try:
            from sqlalchemy import create_engine, text  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "PostgresCheckpointStore requires the [postgres] extra "
                "(pip install 'openminion[postgres]')."
            ) from exc
        if self._engine is None:
            self._engine = create_engine(self.dsn, future=True)
            with self._engine.begin() as conn:
                conn.execute(text(self._DDL))
        return self._engine, text

    def put(self, record: CheckpointRecord) -> None:
        engine, text = self._connect()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO openminion_checkpoints "
                    "(key, version, payload, metadata, updated_at) "
                    "VALUES (:k, :v, CAST(:p AS JSONB), CAST(:m AS JSONB), NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET "
                    "version = EXCLUDED.version, "
                    "payload = EXCLUDED.payload, "
                    "metadata = EXCLUDED.metadata, "
                    "updated_at = NOW()"
                ),
                {
                    "k": record.key,
                    "v": record.version,
                    "p": json.dumps(record.payload),
                    "m": json.dumps(record.metadata),
                },
            )

    def get(self, key: str) -> CheckpointRecord | None:
        engine, text = self._connect()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT key, version, payload, metadata "
                    "FROM openminion_checkpoints WHERE key = :k"
                ),
                {"k": key},
            ).first()
        if row is None:
            return None
        return CheckpointRecord(
            key=row.key,
            version=int(row.version),
            payload=row.payload
            if isinstance(row.payload, dict)
            else json.loads(row.payload),
            metadata=row.metadata
            if isinstance(row.metadata, dict)
            else json.loads(row.metadata or "{}"),
        )

    def list(self, *, prefix: str = "") -> list[str]:
        engine, text = self._connect()
        with engine.connect() as conn:
            if prefix:
                rows = conn.execute(
                    text(
                        "SELECT key FROM openminion_checkpoints "
                        "WHERE key LIKE :p ORDER BY key"
                    ),
                    {"p": f"{prefix}%"},
                ).all()
            else:
                rows = conn.execute(
                    text("SELECT key FROM openminion_checkpoints ORDER BY key")
                ).all()
        return [row.key for row in rows]

    def delete(self, key: str) -> bool:
        engine, text = self._connect()
        with engine.begin() as conn:
            result = conn.execute(
                text("DELETE FROM openminion_checkpoints WHERE key = :k"),
                {"k": key},
            )
            return bool(result.rowcount)


class RedisCheckpointStore:
    """Redis-backed CheckpointStore.

    Uses the ``redis-py`` package. Keys are namespaced under
    ``openminion:checkpoint:<key>`` so they don't collide with anything
    else in the same Redis instance.
    """

    _KEY_NAMESPACE = "openminion:checkpoint:"

    def __init__(self, *, url: str) -> None:
        self.url = url
        self._client = None

    def _connect(self):
        try:
            import redis  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "RedisCheckpointStore requires redis-py (pip install redis>=5)."
            ) from exc
        if self._client is None:
            self._client = redis.Redis.from_url(self.url, decode_responses=True)
        return self._client

    def _ns(self, key: str) -> str:
        return f"{self._KEY_NAMESPACE}{key}"

    def put(self, record: CheckpointRecord) -> None:
        client = self._connect()
        payload = {
            "version": record.version,
            "payload": record.payload,
            "metadata": record.metadata,
        }
        client.set(self._ns(record.key), json.dumps(payload))

    def get(self, key: str) -> CheckpointRecord | None:
        client = self._connect()
        raw = client.get(self._ns(key))
        if raw is None:
            return None
        payload = json.loads(raw)
        return CheckpointRecord(
            key=key,
            version=int(payload.get("version") or 1),
            payload=payload.get("payload") or {},
            metadata=payload.get("metadata") or {},
        )

    def list(self, *, prefix: str = "") -> list[str]:
        client = self._connect()
        pattern = f"{self._KEY_NAMESPACE}{prefix}*"
        return sorted(
            key.removeprefix(self._KEY_NAMESPACE)
            for key in client.scan_iter(match=pattern)
        )

    def delete(self, key: str) -> bool:
        client = self._connect()
        return bool(client.delete(self._ns(key)))


def build_checkpoint_store(spec: str) -> CheckpointStore:
    """Factory: ``memory``, ``postgres://...``, ``redis://...``."""

    normalized = (spec or "").strip()
    if not normalized or normalized == "memory":
        return InMemoryCheckpointStore()
    if normalized.startswith(("postgres://", "postgresql://")):
        return PostgresCheckpointStore(dsn=normalized)
    if normalized.startswith(("redis://", "rediss://")):
        return RedisCheckpointStore(url=normalized)
    raise ValueError(
        f"unrecognized checkpoint spec {spec!r}; expected one of "
        "'memory', 'postgres://...', 'postgresql://...', 'redis://...'."
    )
