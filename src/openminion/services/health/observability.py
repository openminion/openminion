import json
import sqlite3
from pathlib import Path
from typing import Any, Dict

from openminion.base.config import EnvironmentConfig
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from .types import HealthCheck


def _build_brain_llm_mode_observability_check(
    *,
    storage_path: Path,
    probe_session_id: str | None,
    event_limit: int,
    env_config: EnvironmentConfig,
) -> HealthCheck:
    sessions_db_path = resolve_brain_sessions_db_path(storage_path=storage_path)
    normalized_probe = str(probe_session_id or "").strip() or None
    details: Dict[str, Any] = {
        "sessions_db_path": str(sessions_db_path),
        "probe_session_id": normalized_probe or "",
        "event_limit": int(event_limit),
    }

    if not sessions_db_path.exists():
        return HealthCheck(
            id="runtime.brain.llm_mode_observability",
            status="warn",
            message="Brain sessions database not found; cannot verify LLM/mode activity yet.",
            details=details,
        )

    try:
        conn = sqlite3.connect(str(sessions_db_path))
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        details["error"] = str(exc)
        return HealthCheck(
            id="runtime.brain.llm_mode_observability",
            status="warn",
            message=f"Could not open brain sessions database: {exc}",
            details=details,
        )

    try:
        if not _sqlite_table_exists(conn, "session_events"):
            return HealthCheck(
                id="runtime.brain.llm_mode_observability",
                status="warn",
                message="Brain session_events table is unavailable; telemetry coverage unknown.",
                details=details,
            )

        query = """
            SELECT event_type, payload_json
            FROM session_events
            {where_clause}
            ORDER BY seq DESC
            LIMIT ?
        """
        params: list[Any] = [max(1, int(event_limit))]
        where_clause = ""
        if normalized_probe:
            where_clause = "WHERE session_id = ?"
            params.insert(0, normalized_probe)

        event_rows = conn.execute(
            query.format(where_clause=where_clause),
            tuple(params),
        ).fetchall()

        (
            event_counts,
            decision_mode_counts,
            llm_call_counts_by_purpose,
            recursive_source_counts,
        ) = _collect_event_observability_counts(event_rows)

        details["event_counts"] = dict(event_counts)
        details["decision_mode_counts"] = dict(sorted(decision_mode_counts.items()))
        details["llm_call_counts_by_purpose"] = dict(
            sorted(llm_call_counts_by_purpose.items())
        )
        details["recursive_source_counts"] = dict(
            sorted(recursive_source_counts.items())
        )

        brain_mode_counts = _collect_brain_mode_counts(
            conn=conn,
            probe_session_id=normalized_probe,
            event_limit=max(1, int(event_limit)),
        )
        details["brain_mode_counts"] = dict(sorted(brain_mode_counts.items()))

        llm_pipeline_complete = (
            int(event_counts["llm.call.started"]) > 0
            and int(event_counts["context.manifest.created"]) > 0
            and int(event_counts["llm.call.completed"]) > 0
        )
        details["llm_pipeline_complete"] = bool(llm_pipeline_complete)

        plan_mode_seen = int(decision_mode_counts.get("plan", 0)) > 0
        details["plan_mode_seen"] = bool(plan_mode_seen)
        recursive_started = int(event_counts["brain.recursive_turn.started"])
        details["recursive_turn_seen"] = recursive_started > 0

        issues = _resolve_observability_issues(
            event_counts=event_counts,
            brain_mode_counts=brain_mode_counts,
        )
        # Plan mode is optional for simple Q&A turns; enforce only via explicit env gate.
        details["plan_mode_optional"] = True

        require_llm_activity = _env_flag(
            env_config,
            "OPENMINION_HEALTH_REQUIRE_LLM_ACTIVITY",
        )
        require_plan_decisions = _env_flag(
            env_config,
            "OPENMINION_HEALTH_REQUIRE_PLAN_DECISIONS",
        )
        require_real_rlm_autonomy = _env_flag(
            env_config,
            "OPENMINION_HEALTH_REQUIRE_REAL_RLM_AUTONOMY",
        )
        if require_llm_activity and "no_llm_call_events" in issues:
            return HealthCheck(
                id="runtime.brain.llm_mode_observability",
                status="fail",
                message="LLM activity requirement failed: no llm.call.* events found.",
                details=details,
            )
        if require_plan_decisions and not plan_mode_seen:
            return HealthCheck(
                id="runtime.brain.llm_mode_observability",
                status="fail",
                message="Plan decision requirement failed: no `brain.decide mode=plan` events found.",
                details=details,
            )
        if (
            require_real_rlm_autonomy
            and recursive_started > 0
            and int(recursive_source_counts.get("real_rlm", 0)) == 0
        ):
            return HealthCheck(
                id="runtime.brain.llm_mode_observability",
                status="fail",
                message="Autonomy authenticity requirement failed: no `real_rlm` recursive source observed.",
                details=details,
            )

        if issues:
            details["issues"] = list(issues)
            return HealthCheck(
                id="runtime.brain.llm_mode_observability",
                status="warn",
                message="Brain telemetry is present but LLM/mode coverage is incomplete.",
                details=details,
            )

        return HealthCheck(
            id="runtime.brain.llm_mode_observability",
            status="ok",
            message="Brain telemetry confirms LLM activity and mode usage.",
            details=details,
        )
    except Exception as exc:
        details["error"] = str(exc)
        return HealthCheck(
            id="runtime.brain.llm_mode_observability",
            status="warn",
            message=f"Failed to evaluate brain telemetry coverage: {exc}",
            details=details,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _collect_event_observability_counts(
    event_rows: list[sqlite3.Row],
) -> tuple[Dict[str, int], Dict[str, int], Dict[str, int], Dict[str, int]]:
    event_counts = {
        "llm.call.started": 0,
        "context.manifest.created": 0,
        "llm.call.completed": 0,
        "brain.decide": 0,
        "brain.recursive_turn.started": 0,
        "brain.recursive_turn.completed": 0,
        "brain.recursive_turn.error": 0,
    }
    decision_mode_counts: Dict[str, int] = {}
    llm_call_counts_by_purpose: Dict[str, int] = {
        "decide": 0,
        "plan": 0,
        "reflect": 0,
        "follow_up": 0,
    }
    recursive_source_counts: Dict[str, int] = {}
    for row in event_rows:
        event_type = str(row["event_type"] or "").strip()
        if event_type in event_counts:
            event_counts[event_type] += 1
        if event_type == "llm.call.completed":
            payload = _safe_json_object(row["payload_json"])
            purpose = _normalize_llm_call_purpose(str(payload.get("purpose", "")))
            if purpose is not None:
                llm_call_counts_by_purpose[purpose] = (
                    int(llm_call_counts_by_purpose.get(purpose, 0)) + 1
                )
        if event_type.startswith("brain.recursive_turn."):
            payload = _safe_json_object(row["payload_json"])
            source = str(payload.get("source", "")).strip().lower()
            if source:
                recursive_source_counts[source] = (
                    recursive_source_counts.get(source, 0) + 1
                )
        if event_type == "brain.decide":
            payload = _safe_json_object(row["payload_json"])
            mode = str(payload.get("mode", "")).strip().lower()
            if mode:
                decision_mode_counts[mode] = decision_mode_counts.get(mode, 0) + 1
    return (
        event_counts,
        decision_mode_counts,
        llm_call_counts_by_purpose,
        recursive_source_counts,
    )


def _resolve_observability_issues(
    *,
    event_counts: Dict[str, int],
    brain_mode_counts: Dict[str, int],
) -> list[str]:
    issues: list[str] = []
    if (
        int(event_counts["llm.call.started"]) == 0
        or int(event_counts["llm.call.completed"]) == 0
    ):
        issues.append("no_llm_call_events")
    if (
        int(event_counts["llm.call.started"]) > 0
        and int(event_counts["context.manifest.created"]) == 0
    ):
        issues.append("missing_context_manifest")
    if int(event_counts["brain.decide"]) == 0:
        issues.append("no_brain_decide_events")
    if not brain_mode_counts:
        issues.append("no_persisted_brain_modes")
    return issues


def _collect_brain_mode_counts(
    *,
    conn: sqlite3.Connection,
    probe_session_id: str | None,
    event_limit: int,
) -> Dict[str, int]:
    if not _sqlite_table_exists(conn, "working_state"):
        return {}

    mode_counts: Dict[str, int] = {}
    seen_sessions: set[str] = set()
    if probe_session_id:
        rows = conn.execute(
            """
            SELECT session_id, state_inline_json
            FROM working_state
            WHERE session_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (probe_session_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT session_id, state_inline_json
            FROM working_state
            ORDER BY version DESC
            LIMIT ?
            """,
            (max(1, int(event_limit)),),
        ).fetchall()

    for row in rows:
        session_id = str(row["session_id"] or "").strip()
        if not session_id or session_id in seen_sessions:
            continue
        seen_sessions.add(session_id)
        payload = _safe_json_object(row["state_inline_json"])
        mode = str(payload.get("mode", "")).strip().lower()
        if not mode:
            continue
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
    return mode_counts


def _resolve_brain_observability_event_limit(env_config: EnvironmentConfig) -> int:
    return max(20, env_config.get_int("OPENMINION_HEALTH_BRAIN_EVENT_LIMIT", 200))


def _env_flag(
    env_config: EnvironmentConfig,
    name: str,
    *,
    default: bool = False,
) -> bool:
    return env_config.get_bool(name, default)


def _safe_json_object(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_llm_call_purpose(raw_purpose: str) -> str | None:
    normalized = str(raw_purpose or "").strip().lower()
    if normalized in {"decide", "plan", "reflect"}:
        return normalized
    if normalized in {"respond_followup", "follow_up"}:
        return "follow_up"
    return None


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (str(table_name),),
    ).fetchone()
    return row is not None
