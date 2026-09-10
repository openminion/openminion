from openminion.base.config.base import ConfigError

TURN_USAGE_DISPLAY_VALUES = ("off", "total", "input_output", "input_output_calls")


def normalize_turn_usage_display(value: object, *, field_path: str) -> str:
    if not isinstance(value, str) or value not in TURN_USAGE_DISPLAY_VALUES:
        supported = ", ".join(TURN_USAGE_DISPLAY_VALUES)
        raise ConfigError(f"{field_path} must be one of: {supported}.")
    return value
