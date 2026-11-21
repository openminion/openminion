"""Next-step guidance for a first run with no config file."""

from openminion.base.config.manager import ConfigManagerError

CONFIG_MISSING_PREFIX = "config file not found:"
CONFIG_MISSING_HINT = (
    "No config file yet. Run `openminion setup` to choose a provider, or "
    "`openminion config init` to create the default config."
)


def is_missing_config_error(exc: BaseException) -> bool:
    """True when the config manager reported an absent config file.

    ``ConfigManager`` raises this one message from a single site; the base
    package has no headroom for a dedicated exception type, so the check is
    pinned to the type plus that message.
    """

    return isinstance(exc, ConfigManagerError) and str(exc).startswith(
        CONFIG_MISSING_PREFIX
    )


def config_missing_hint() -> str:
    """Return the next-step line for a missing config file."""

    return CONFIG_MISSING_HINT


__all__ = [
    "CONFIG_MISSING_HINT",
    "CONFIG_MISSING_PREFIX",
    "config_missing_hint",
    "is_missing_config_error",
]
