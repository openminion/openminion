from openminion.tools.constants import (
    TOOL_REASON_RECORD_NOT_FOUND as TASK_REASON_RECORD_NOT_FOUND,
    TOOL_REASON_STORAGE_EXEC_ERROR as TASK_REASON_STORAGE_EXEC_ERROR,
    TOOL_REASON_STORAGE_UNAVAILABLE as TASK_REASON_STORAGE_UNAVAILABLE,
    TOOL_REASON_STORAGE_UNCONFIGURED as TASK_REASON_STORAGE_UNCONFIGURED,
)

DEFAULT_TASK_NAME_MAX_CHARS = 60
DEFAULT_CONSOLIDATION_BATCH_LIMIT = 12
DEFAULT_CONSOLIDATION_INTERVAL_HOURS = 24
DEFAULT_CONSOLIDATION_MAX_ITERATIONS = 2
DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS = 30
DEFAULT_WATCH_MAX_CHECKS = 6
DEFAULT_WATCH_TIMEOUT_SECONDS = 60
DEFAULT_WATCH_MAX_ITERATIONS = 3
DEFAULT_WATCH_TTL_MINUTES = 60
# runtime-side cap for `ReviewedPrV1.summary` so the model cannot
# blow the artifact budget by emitting a giant summary string.
PR_REVIEW_SUMMARY_MAX_CHARS = 1000
# runtime-side cap for the announce delivery summary line.
PR_REVIEW_ANNOUNCE_MAX_CHARS = 200
CONSOLIDATION_PAYLOAD_KEY = "_openminion_memory_consolidation"
FIRST_RUN_PENDING_STATE = "pending"
FIRST_RUN_PENDING_NOTE = "No runs recorded yet. Scheduled runs execute only while the openminion daemon is running."
WATCH_PAYLOAD_KEY = "_openminion_watch"
WATCH_TURN_KIND_CHECK = "check"
WATCH_TURN_KIND_ACTION = "action"
WATCH_DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = (
    "file.read",
    "file.list_dir",
    "file.find",
    "web.fetch",
    "web.search",
    "exec.run",
    "time",
)
EVERY_UNIT_TO_MS: dict[str, int] = {
    "ms": 1,
    "millisecond": 1,
    "milliseconds": 1,
    "s": 1_000,
    "sec": 1_000,
    "secs": 1_000,
    "second": 1_000,
    "seconds": 1_000,
    "m": 60_000,
    "min": 60_000,
    "mins": 60_000,
    "minute": 60_000,
    "minutes": 60_000,
    "h": 3_600_000,
    "hr": 3_600_000,
    "hrs": 3_600_000,
    "hour": 3_600_000,
    "hours": 3_600_000,
    "d": 86_400_000,
    "day": 86_400_000,
    "days": 86_400_000,
    "interval_milliseconds": 1,
    "interval_seconds": 1_000,
    "interval_minutes": 60_000,
    "interval_hours": 3_600_000,
    "interval_days": 86_400_000,
    "every_milliseconds": 1,
    "every_seconds": 1_000,
    "every_minutes": 60_000,
    "every_hours": 3_600_000,
    "every_days": 86_400_000,
}

__all__ = [
    "DEFAULT_TASK_NAME_MAX_CHARS",
    "DEFAULT_CONSOLIDATION_BATCH_LIMIT",
    "DEFAULT_CONSOLIDATION_INTERVAL_HOURS",
    "DEFAULT_CONSOLIDATION_MAX_ITERATIONS",
    "DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS",
    "DEFAULT_WATCH_MAX_CHECKS",
    "DEFAULT_WATCH_TIMEOUT_SECONDS",
    "DEFAULT_WATCH_MAX_ITERATIONS",
    "DEFAULT_WATCH_TTL_MINUTES",
    "CONSOLIDATION_PAYLOAD_KEY",
    "EVERY_UNIT_TO_MS",
    "FIRST_RUN_PENDING_NOTE",
    "FIRST_RUN_PENDING_STATE",
    "TASK_REASON_RECORD_NOT_FOUND",
    "TASK_REASON_STORAGE_EXEC_ERROR",
    "TASK_REASON_STORAGE_UNAVAILABLE",
    "TASK_REASON_STORAGE_UNCONFIGURED",
    "WATCH_DEFAULT_ALLOWED_TOOLS",
    "WATCH_PAYLOAD_KEY",
    "WATCH_TURN_KIND_ACTION",
    "WATCH_TURN_KIND_CHECK",
]
