from pathlib import Path

from openminion.base.constants import (
    OPENMINION_DATA_ROOT_ENV,
    OPENMINION_MODULE_STANDALONE_ENV,
)
from openminion.modules.paths import (
    CONTROLPLANE_DIRNAME,
    STANDALONE_CONTROLPLANE_DIRNAME,
)

# Valid access policy values.
ALLOWED_POLICIES: set[str] = {"allow", "deny", "allowlist"}

# Valid channel mode values.
ALLOWED_MODES: set[str] = {"polling", "webhook"}

# webhook HTTP listener fixed internal values.
WEBHOOK_LISTENER_DEFAULT_PATH = "/telegram/webhook"
# Defensive cap on inbound webhook body size (1 MiB). Telegram update
WEBHOOK_LISTENER_MAX_BODY_BYTES = 1 * 1024 * 1024
# Header carrying the Telegram secret token; required when
# ``WebhookConfig.secret`` is set.
WEBHOOK_LISTENER_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"

ACCESS_REASON_OK = "ok"
ACCESS_REASON_DM_POLICY_DENY = "dm_policy_deny"
ACCESS_REASON_DM_ALLOWLIST_MISS = "dm_allowlist_miss"
ACCESS_REASON_GROUP_POLICY_DENY = "group_policy_deny"
ACCESS_REASON_GROUP_ALLOWLIST_MISS = "group_allowlist_miss"
ACCESS_REASON_MENTION_REQUIRED = "mention_required"
ACCESS_REASON_TOPIC_REQUIRED = "topic_required"
ACCESS_REASON_TOPIC_NOT_ALLOWED = "topic_not_allowed"
ACCESS_REASON_PAIRED_BINDING = "paired_binding"
ROUTE_REASON_RUNTIME_DISPATCH = "runtime_dispatch"
ROUTE_REASON_RUNTIME_DISPATCH_FAILED = "runtime_dispatch_failed"

PAIRING_MODE_REQUIRED = "required"
PAIRING_MODE_OFF = "off"
PAIRING_MODES: frozenset[str] = frozenset({PAIRING_MODE_REQUIRED, PAIRING_MODE_OFF})

REPLY_MODE_TO_USER = "reply_to_user"
REPLY_MODE_TO_THREAD = "reply_to_thread"
REPLY_MODE_NONE = "none"
REPLY_MODES: frozenset[str] = frozenset(
    {REPLY_MODE_TO_USER, REPLY_MODE_TO_THREAD, REPLY_MODE_NONE}
)

# Path Layout
DEFAULT_STANDALONE_POLL_STATE_SUBPATH = (
    Path(STANDALONE_CONTROLPLANE_DIRNAME) / "telegram-poll-state.db"
)
DEFAULT_INTEGRATED_POLL_STATE_SUBPATH = (
    Path(CONTROLPLANE_DIRNAME) / "telegram-poll-state.db"
)
DEFAULT_HOME_ROOT_POLL_STATE_SUBPATH = (
    Path(".openminion") / CONTROLPLANE_DIRNAME / "telegram-poll-state.db"
)

__all__ = [
    "ACCESS_REASON_DM_ALLOWLIST_MISS",
    "ACCESS_REASON_DM_POLICY_DENY",
    "ACCESS_REASON_GROUP_ALLOWLIST_MISS",
    "ACCESS_REASON_GROUP_POLICY_DENY",
    "ACCESS_REASON_MENTION_REQUIRED",
    "ACCESS_REASON_OK",
    "ACCESS_REASON_PAIRED_BINDING",
    "ACCESS_REASON_TOPIC_NOT_ALLOWED",
    "ACCESS_REASON_TOPIC_REQUIRED",
    "ALLOWED_MODES",
    "ALLOWED_POLICIES",
    "DEFAULT_HOME_ROOT_POLL_STATE_SUBPATH",
    "DEFAULT_INTEGRATED_POLL_STATE_SUBPATH",
    "DEFAULT_STANDALONE_POLL_STATE_SUBPATH",
    "OPENMINION_DATA_ROOT_ENV",
    "OPENMINION_MODULE_STANDALONE_ENV",
    "PAIRING_MODE_OFF",
    "PAIRING_MODE_REQUIRED",
    "PAIRING_MODES",
    "REPLY_MODE_NONE",
    "REPLY_MODE_TO_THREAD",
    "REPLY_MODE_TO_USER",
    "REPLY_MODES",
    "ROUTE_REASON_RUNTIME_DISPATCH",
    "ROUTE_REASON_RUNTIME_DISPATCH_FAILED",
]
