from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openminion.base.config.core import resolve_default_agent_id
from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION


class RuntimeSystemProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(self, runtime: Any, *, started_at: datetime | None = None) -> None:
        self._runtime = runtime
        self._started_at = started_at or datetime.now(timezone.utc)

    def get_daemon_status(self) -> dict[str, Any]:
        runtime = self._runtime
        manager = getattr(runtime, "runtime_manager", None)
        mode = "in-process"
        if manager is not None:
            mode = "in-process(runtime-manager)"

        endpoint = "—"
        try:
            runtime_cfg = getattr(getattr(runtime, "config", None), "runtime", None)
            host = str(getattr(runtime_cfg, "ipc_host", "")).strip()
            port = int(getattr(runtime_cfg, "ipc_port", 0) or 0)
            if host and port > 0:
                endpoint = f"{host}:{port}"
        except (AttributeError, TypeError, ValueError):
            endpoint = "—"

        uptime = self._uptime_text()
        return {
            "mode": mode,
            "endpoint": endpoint,
            "pid": os.getpid(),
            "uptime": uptime,
        }

    def get_storage_stats(self) -> dict[str, Any]:
        runtime = self._runtime
        storage_path = Path(
            str(getattr(runtime, "storage_path", "") or "")
        ).expanduser()
        db_size = "—"
        if storage_path.exists():
            db_size = self._human_size(storage_path.stat().st_size)

        sessions = getattr(runtime, "sessions", None)
        conn = self._session_conn(sessions)
        session_count = 0
        if sessions is not None and callable(getattr(sessions, "count_sessions", None)):
            try:
                session_count = int(sessions.count_sessions())
            except (TypeError, ValueError):
                session_count = 0

        event_count = self._sqlite_count(conn, "events")
        memory_count = self._memory_record_count(getattr(runtime, "memory_root", None))

        return {
            "db_size": db_size,
            "session_count": session_count,
            "event_count": event_count,
            "memory_count": memory_count,
        }

    def get_agent_info(self) -> dict[str, Any]:
        runtime = self._runtime
        config = getattr(runtime, "config", None)
        agent_cfg = None
        agent_id = ""
        try:
            agent_id = resolve_default_agent_id(config)
            agent_cfg = getattr(config, "agents", {}).get(agent_id)
        except (AttributeError, TypeError, ValueError):
            agent_cfg = None
        provider = str(getattr(agent_cfg, "provider", "") or "")
        model = str(getattr(agent_cfg, "model", "") or "")

        runtime_info = {}
        get_runtime_info = getattr(runtime, "get_agent_runtime_info", None)
        if callable(get_runtime_info):
            try:
                runtime_info = get_runtime_info(
                    str(getattr(agent_cfg, "name", "") or agent_id or "")
                )
            except Exception:
                runtime_info = {}

        return {
            "model": model or "—",
            "runtime_mode": str(runtime_info.get("runtime_mode") or "unknown"),
            "brain_mode": "contextctl_authoritative",
            "provider": provider or "—",
        }

    def get_telemetry_summary(self) -> dict[str, Any]:
        sessions = getattr(self._runtime, "sessions", None)
        conn = self._session_conn(sessions)
        if not isinstance(conn, sqlite3.Connection):
            return {
                "turns": "—",
                "tool_calls": "—",
                "errors": "—",
                "avg_latency": "—",
            }

        turns = self._count_recent(conn, "messages", "created_at")
        tool_calls = self._count_recent(
            conn,
            "events",
            "created_at",
            where="event_type LIKE 'tool.%'",
        )
        errors = self._count_recent(
            conn,
            "events",
            "created_at",
            where="event_type LIKE '%error%'",
        )

        avg_latency = self._average_recent_latency(conn)

        return {
            "turns": turns,
            "tool_calls": tool_calls,
            "errors": errors,
            "avg_latency": avg_latency,
        }

    def get_plugin_status(self) -> list[dict[str, Any]]:
        plugins = getattr(self._runtime, "plugins", None)
        if plugins is None:
            return []

        names_fn = getattr(plugins, "names", None)
        if callable(names_fn):
            try:
                names = names_fn()
                if isinstance(names, list):
                    return [
                        {"name": str(name), "enabled": True}
                        for name in names
                        if str(name).strip()
                    ]
            except Exception:
                pass

        manifests_fn = getattr(plugins, "manifests", None)
        if callable(manifests_fn):
            try:
                manifests = manifests_fn()
                if isinstance(manifests, list):
                    out: list[dict[str, Any]] = []
                    for manifest in manifests:
                        name = str(
                            getattr(manifest, "id", "") or getattr(manifest, "name", "")
                        ).strip()
                        if name:
                            out.append({"name": name, "enabled": True})
                    return out
            except Exception:
                pass
        return []

    def get_sidecar_status(self) -> dict[str, Any]:
        manager = self._sidecar_manager()
        name = self._first_sidecar_name(manager)
        if not name:
            return self._default_sidecar_status()
        try:
            status = manager.status(name)
        except Exception:
            status = {}
        try:
            consent = manager.consent(name)
        except Exception:
            consent = None
        return {
            "name": name,
            "running": bool(status.get("pid_alive") or status.get("ok")),
            "pid": status.get("pid") or "—",
            "consent": self._consent_label(consent),
        }

    def set_sidecar_consent(self, approved: bool) -> dict[str, Any]:
        manager = self._sidecar_manager()
        name = self._first_sidecar_name(manager)
        if not name:
            return self.get_sidecar_status()
        try:
            if approved:
                manager.approve(name)
            else:
                manager.deny(name)
        except Exception:
            return self.get_sidecar_status()
        return self.get_sidecar_status()

    def start_sidecar(self) -> dict[str, Any]:
        manager = self._sidecar_manager()
        name = self._first_sidecar_name(manager)
        if not name:
            return self.get_sidecar_status()
        try:
            manager.ensure_started(name=name, interactive=False)
        except Exception:
            return self.get_sidecar_status()
        return self.get_sidecar_status()

    def stop_sidecar(self) -> dict[str, Any]:
        manager = self._sidecar_manager()
        name = self._first_sidecar_name(manager)
        if not name:
            return self.get_sidecar_status()
        try:
            manager.stop(name=name)
        except Exception:
            return self.get_sidecar_status()
        return self.get_sidecar_status()

    def _uptime_text(self) -> str:
        delta = datetime.now(timezone.utc) - self._started_at
        seconds = max(0, int(delta.total_seconds()))
        hours, rem = divmod(seconds, 3600)
        minutes, _ = divmod(rem, 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{max(1, minutes)}m"

    @staticmethod
    def _human_size(size_bytes: int) -> str:
        units = ["B", "KB", "MB", "GB"]
        value = float(max(0, size_bytes))
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)}{unit}"
                return f"{value:.1f}{unit}"
            value /= 1024.0
        return f"{int(size_bytes)}B"

    @staticmethod
    def _sqlite_count(conn: Any, table: str) -> int | str:
        if not isinstance(conn, sqlite3.Connection):
            return "—"
        try:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        except sqlite3.Error:
            return "—"
        if row is None:
            return 0
        return int(row[0])

    @staticmethod
    def _session_conn(sessions: Any) -> sqlite3.Connection | None:
        if sessions is None:
            return None
        try:
            conn = getattr(sessions, "_conn", None)
        except AttributeError:
            return None
        if isinstance(conn, sqlite3.Connection):
            return conn
        return None

    @staticmethod
    def _memory_record_count(memory_root: Any) -> int | str:
        if memory_root is None:
            return "—"
        db_path = Path(str(memory_root)).expanduser() / "memory.db"
        if not db_path.exists():
            return 0
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM memory_records WHERE is_deleted = 0"
                ).fetchone()
            finally:
                conn.close()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return "—"
        if row is None:
            return 0
        return int(row[0])

    @staticmethod
    def _count_recent(
        conn: sqlite3.Connection,
        table: str,
        created_col: str,
        *,
        where: str | None = None,
        window_minutes: int = 60,
    ) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=max(1, int(window_minutes)))
        ).isoformat()
        clause = f" AND {where}" if where else ""
        row = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {created_col} >= ?{clause}",
            (cutoff,),
        ).fetchone()
        if row is None:
            return 0
        return int(row[0])

    @staticmethod
    def _average_recent_latency(
        conn: sqlite3.Connection, *, window_minutes: int = 60
    ) -> str:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=max(1, int(window_minutes)))
        ).isoformat()
        try:
            rows = conn.execute(
                """
                SELECT event_type, created_at
                FROM events
                WHERE created_at >= ?
                ORDER BY created_at ASC, id ASC
                """,
                (cutoff,),
            ).fetchall()
        except sqlite3.Error:
            return "—"
        if not rows:
            return "—"

        pending: dict[str, list[datetime]] = {}
        durations_ms: list[float] = []
        start_suffixes = (".started", ".request", ".queued")
        end_suffixes = (".completed", ".finished", ".success", ".response")

        for row in rows:
            event_type = str(row[0] or "").strip()
            created_at = RuntimeSystemProvider._parse_iso_datetime(str(row[1] or ""))
            if not event_type or created_at is None:
                continue
            base = RuntimeSystemProvider._event_latency_base(event_type)
            if not base:
                continue
            if event_type.endswith(start_suffixes):
                pending.setdefault(base, []).append(created_at)
                continue
            if not event_type.endswith(end_suffixes):
                continue
            queue = pending.get(base)
            if not queue:
                continue
            started_at = queue.pop(0)
            delta_ms = max(0.0, (created_at - started_at).total_seconds() * 1000.0)
            durations_ms.append(delta_ms)

        if not durations_ms:
            return "—"

        avg_ms = sum(durations_ms) / len(durations_ms)
        if avg_ms >= 1000.0:
            return f"{avg_ms / 1000.0:.1f}s"
        return f"{int(round(avg_ms))}ms"

    def _sidecar_manager(self) -> Any | None:
        manager = getattr(self._runtime, "sidecar_manager", None)
        if manager is not None:
            return manager
        runtime_manager = getattr(self._runtime, "runtime_manager", None)
        if runtime_manager is not None:
            return getattr(runtime_manager, "sidecar_manager", None)
        return None

    @staticmethod
    def _default_sidecar_status() -> dict[str, Any]:
        return {
            "name": "pinchtab",
            "running": False,
            "pid": "—",
            "consent": "unknown",
        }

    @staticmethod
    def _first_sidecar_name(manager: Any | None) -> str:
        if manager is None:
            return ""
        try:
            names = list(manager.list())
        except Exception:
            return ""
        return str(names[0]) if names else ""

    @staticmethod
    def _event_latency_base(event_type: str) -> str:
        text = str(event_type or "").strip()
        for suffix in (
            ".started",
            ".request",
            ".queued",
            ".completed",
            ".finished",
            ".success",
            ".response",
        ):
            if text.endswith(suffix):
                return text[: -len(suffix)]
        return ""

    @staticmethod
    def _consent_label(consent: Any) -> str:
        if consent is None:
            return "unknown"
        approved = bool(getattr(consent, "approved", False))
        return "approved" if approved else "denied"

    @staticmethod
    def _parse_iso_datetime(raw: str) -> datetime | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
