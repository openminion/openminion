"""Constants for the Slack controlplane channel."""

CHANNEL_ID = "slack"

MODE_SOCKET = "socket"
MODE_HTTP = "http"
SUPPORTED_MODES = frozenset({MODE_SOCKET, MODE_HTTP})

DEFAULT_STATE_DB_NAME = "slack-state.db"
DEFAULT_MAX_MESSAGE_CHARS = 3900
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_S = 1.0

ROUTE_REASON_DUPLICATE_EVENT = "duplicate_event"
ROUTE_REASON_ACCESS_DENIED = "access_denied"
ROUTE_REASON_RUNTIME_DISPATCH_FAILED = "runtime_dispatch_failed"
