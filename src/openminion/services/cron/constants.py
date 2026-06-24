# Valid schedule kinds.
ALLOWED_SCHEDULE_KINDS: set[str] = {"at", "every", "cron"}

# Valid wake modes.
ALLOWED_WAKE_MODES: set[str] = {"now", "next-heartbeat"}

# Valid session targets.
ALLOWED_SESSION_TARGETS: set[str] = {"main", "isolated", "agent_session"}

# Valid payload kinds.
PAYLOAD_KIND_SYSTEM_EVENT: str = "systemEvent"
ALLOWED_PAYLOAD_KINDS: set[str] = {
    PAYLOAD_KIND_SYSTEM_EVENT,
    "agentTurn",
    "agentIdleTick",
}

# Valid delivery modes.
ALLOWED_DELIVERY_MODES: set[str] = {"none", "announce", "webhook"}

# Valid misfire policy modes.
ALLOWED_MISFIRE_MODES: set[str] = {"skip", "run_once", "catch_up"}

# Default stagger in ms applied to top-of-hour cron expressions.
DEFAULT_TOP_OF_HOUR_STAGGER_MS: int = 300_000
