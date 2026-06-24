from openminion.base.config import ActionPolicyConfig
from openminion.base.config.action_policy import map_action_policy_mode

from ..models import PolicyConfig


def policy_config_from_action_policy(
    action_policy: ActionPolicyConfig,
) -> PolicyConfig:
    defaults = PolicyConfig()
    return PolicyConfig(
        mode=map_action_policy_mode(action_policy.mode),  # type: ignore[arg-type]
        default_action=str(
            getattr(action_policy, "default_action", defaults.default_action)
            or defaults.default_action
        ),  # type: ignore[arg-type]
        allow_read_only_without_prompt=bool(
            getattr(
                action_policy,
                "allow_read_only_without_prompt",
                defaults.allow_read_only_without_prompt,
            )
        ),
        affirmative_tokens=list(
            getattr(action_policy, "affirmative_tokens", [])
            or defaults.affirmative_tokens
        ),
        negative_tokens=list(
            getattr(action_policy, "negative_tokens", []) or defaults.negative_tokens
        ),
    )


__all__ = ("policy_config_from_action_policy",)
