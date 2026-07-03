from openminion.base.version import OPENMINION_VERSION

from .config import ControlplaneTelegramConfig, TelegramChannelConfig, load_config
from .interfaces import (
    TELEGRAM_INTERFACE_VERSION,
    ensure_telegram_component_compatibility,
)
from .polling import TelegramPollingRunner

__all__ = [
    "ControlplaneTelegramConfig",
    "TELEGRAM_INTERFACE_VERSION",
    "TelegramChannelConfig",
    "TelegramPollingRunner",
    "ensure_telegram_component_compatibility",
    "load_config",
    "__version__",
]

__version__ = OPENMINION_VERSION
