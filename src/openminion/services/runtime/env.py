from collections.abc import Mapping
import os

from openminion.services.config import resolve_services_env


def apply_runtime_environment(
    env_values: Mapping[str, str], *, overwrite: bool = False
) -> None:
    """Apply runtime environment helper."""
    existing_env = resolve_services_env(process_env=os.environ)
    for raw_key, raw_value in env_values.items():
        key = str(raw_key or "").strip()
        value = str(raw_value or "").strip()
        if not key or not value:
            continue
        if not overwrite and existing_env.has(key):
            continue
        os.environ[key] = value
