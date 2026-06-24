from pathlib import Path

DEFAULT_INTEGRATED_SQLITE_SUBPATH = Path("task") / "task.db"

DEFAULT_TASK_MIN_EVERY_MS: int = 10_000
TASK_REASON_SCHEDULE_INTERVAL_TOO_SHORT = "TASK_SCHEDULE_INTERVAL_TOO_SHORT"
TASK_REASON_RESUME_EXPIRED_ONE_SHOT = "TASK_RESUME_EXPIRED_ONE_SHOT"
TASK_REASON_PAUSED_LEGACY_INTERVAL_TOO_SHORT = "TASK_SCHEDULE_INTERVAL_TOO_SHORT"
TASK_INTERNAL_PAUSE_REASON_KEY = "_openminion_pause_reason"
TASK_INTERNAL_PAUSE_SOURCE_KEY = "_openminion_pause_source"

TASK_PLAN_OUTPUT_SUMMARY_MAX_CHARS: int = 240
TASK_PLAN_TOOL_FAMILIES: frozenset[str] = frozenset(
    {
        "browser",
        "code",
        "exec",
        "fetch",
        "file",
        "ip",
        "location",
        "search",
        "skill",
        "task",
        "time",
        "utility",
        "weather",
        "web",
    }
)
