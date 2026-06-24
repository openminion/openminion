from dataclasses import dataclass

from ..contracts.models import InboundMessage


@dataclass(frozen=True)
class RateLimitPolicy:
    chat_window_s: int = 60
    chat_limit: int = 30
    user_window_s: int = 60
    user_limit: int = 30
    session_window_s: int = 60
    session_limit: int = 40


@dataclass
class ControlPlaneRateLimiter:
    store: object
    policy: RateLimitPolicy = RateLimitPolicy()

    def check(self, inbound: InboundMessage, session_id: str) -> tuple[bool, str]:
        if not hasattr(self.store, "increment_rate_limit"):
            return True, "rate-limit-disabled"

        checks = [
            (
                "chat_id",
                str(inbound.chat_id or inbound.chat_key),
                self.policy.chat_window_s,
                self.policy.chat_limit,
            ),
            (
                "user_id",
                str(inbound.user_id or inbound.user_key),
                self.policy.user_window_s,
                self.policy.user_limit,
            ),
            (
                "session_id",
                str(session_id),
                self.policy.session_window_s,
                self.policy.session_limit,
            ),
        ]

        for key_type, key_id, window_s, limit in checks:
            result = self.store.increment_rate_limit(
                key_type=key_type,
                key_id=key_id,
                window_seconds=window_s,
                limit=limit,
            )
            if not bool(result.get("allowed", False)):
                return False, f"rate limit exceeded for {key_type}"

        return True, "ok"
