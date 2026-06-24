from typing import Any

from openminion.base.config import ConfigManager
from openminion.base.config.interface import ModuleConfigFactory
from openminion.modules.a2a.config import from_base_config as a2a_from_base_config
from openminion.modules.artifact.config import (
    from_base_config as artifact_from_base_config,
)
from openminion.modules.brain.config import from_base_config as brain_from_base_config
from openminion.modules.controlplane.config import (
    from_base_config as controlplane_from_base_config,
)
from openminion.modules.controlplane.channels.telegram.config import (
    from_base_config as controlplane_telegram_from_base_config,
)
from openminion.modules.identity.config import (
    from_base_config as identity_from_base_config,
)
from openminion.modules.llm.config import from_base_config as llm_from_base_config
from openminion.modules.memory.config import from_base_config as memory_from_base_config
from openminion.modules.registry.config import (
    from_base_config as registry_from_base_config,
)
from openminion.modules.retrieve.config import (
    from_base_config as retrieve_from_base_config,
)
from openminion.services.runtime.settings import (
    from_base_config as runtime_from_base_config,
)
from openminion.modules.skill.config import from_base_config as skill_from_base_config

_DEFAULT_FACTORIES: tuple[tuple[str, ModuleConfigFactory[Any]], ...] = (
    ("a2a", a2a_from_base_config),
    ("artifact", artifact_from_base_config),
    ("brain", brain_from_base_config),
    ("identity", identity_from_base_config),
    ("llm", llm_from_base_config),
    ("memory", memory_from_base_config),
    ("registry", registry_from_base_config),
    ("skill", skill_from_base_config),
    ("retrieve", retrieve_from_base_config),
    ("controlplane", controlplane_from_base_config),
    ("controlplane_telegram", controlplane_telegram_from_base_config),
    ("runtime", runtime_from_base_config),
)


def bootstrap_config_manager(manager: ConfigManager) -> ConfigManager:
    for name, factory in _DEFAULT_FACTORIES:
        if manager.is_registered(name):
            continue
        manager.register(name, factory)
    return manager
