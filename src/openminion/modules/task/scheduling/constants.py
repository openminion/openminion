ALLOWED_SCHEDULE_KINDS: set[str] = {"at", "every", "cron"}

ALLOWED_WAKE_MODES: set[str] = {"now", "next-heartbeat"}

ALLOWED_SESSION_TARGETS: set[str] = {"main", "isolated", "agent_session"}

PAYLOAD_KIND_SYSTEM_EVENT: str = "systemEvent"
PAYLOAD_KIND_AGENT_IDLE_TICK: str = "agentIdleTick"
PAYLOAD_KIND_PROJECT_CYCLE: str = "projectCycle"
ALLOWED_PAYLOAD_KINDS: set[str] = {
    PAYLOAD_KIND_SYSTEM_EVENT,
    "agentTurn",
    PAYLOAD_KIND_AGENT_IDLE_TICK,
    PAYLOAD_KIND_PROJECT_CYCLE,
}

ALLOWED_DELIVERY_MODES: set[str] = {"none", "announce", "webhook"}

ALLOWED_MISFIRE_MODES: set[str] = {"skip", "run_once", "catch_up"}

DEFAULT_TOP_OF_HOUR_STAGGER_MS: int = 300_000
