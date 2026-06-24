from openminion.modules.llm.providers.behavior.contracts import (
    ProviderBehaviorProfile,
)


class BehaviorProfileRegistry:
    """Registry keyed by provider behavior profile id."""

    def __init__(self) -> None:
        self._profiles: dict[str, ProviderBehaviorProfile] = {}

    def register(self, profile: ProviderBehaviorProfile) -> None:
        """Register `profile`, overwriting an existing id if present."""

        self._profiles[profile.profile_id] = profile

    def get(self, profile_id: str) -> ProviderBehaviorProfile | None:
        """Return the registered profile for `profile_id`, or `None`."""

        return self._profiles.get(profile_id)

    def ids(self) -> tuple[str, ...]:
        """Return the registered `profile_id`s in insertion order."""

        return tuple(self._profiles.keys())


# Module-level singleton. Tests that need isolation can pass a custom registry.
default_registry = BehaviorProfileRegistry()


def register_behavior_profile(profile: ProviderBehaviorProfile) -> None:
    """Register `profile` into the module-level `default_registry`."""

    default_registry.register(profile)


def _seed_default_profiles() -> None:
    """Pre-register the two seed profiles described in the module docstring."""

    register_behavior_profile(ProviderBehaviorProfile(profile_id="default"))
    register_behavior_profile(
        ProviderBehaviorProfile(
            profile_id="minimax_openai_compat",
            telemetry_labels=("minimax", "openai_dialect"),
        )
    )


_seed_default_profiles()
