import importlib.metadata
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .module import CommandModule, CommandModuleFactory
    from .registry import CommandRegistry

logger = logging.getLogger(__name__)


_ENTRYPOINT_GROUPS = ("openminion.modules.controlplane.commands",)


def _select_command_entry_points(
    eps: object,
) -> list[importlib.metadata.EntryPoint]:
    selected: list[importlib.metadata.EntryPoint] = []
    seen: set[tuple[str, str, str]] = set()

    for group in _ENTRYPOINT_GROUPS:
        if hasattr(eps, "select"):
            group_eps = eps.select(group=group)
        else:
            group_eps = eps.get(group, [])
        for ep in group_eps:
            key = (ep.group, ep.name, ep.value)
            if key in seen:
                continue
            seen.add(key)
            selected.append(ep)
    return selected


def discover_and_register_commands(
    registry: CommandRegistry,
) -> tuple[list[str], list[str]]:
    """Discover command modules via entry points and register their commands."""
    try:
        eps = importlib.metadata.entry_points()
    except Exception as e:
        logger.warning(f"Failed to discover entry points: {e}")
        return [], []

    command_eps = _select_command_entry_points(eps)

    loaded_modules: list[str] = []
    error_modules: list[str] = []

    for ep in command_eps:
        module_name = ep.name
        logger.debug(f"Discovered command module entry point: {module_name}")

        try:
            factory_func = ep.load()
            if not callable(factory_func):
                raise ValueError(
                    f"Entry point {module_name} does not return a callable factory"
                )

            module_factory: CommandModuleFactory = factory_func
            module_instance: CommandModule = module_factory()

            module_error_count = 0
            for spec in module_instance.get_commands():
                try:
                    registry.register_command_spec(spec)
                except Exception as reg_err:
                    logger.error(
                        f"Failed to register command '{spec.name}' from module '{module_name}': {reg_err}"
                    )
                    module_error_count += 1

            if module_error_count == 0:
                loaded_modules.append(module_name)
                logger.info(f"Successfully loaded command module: {module_name}")
            else:
                error_modules.append(
                    f"{module_name}({module_error_count} commands failed)"
                )
                logger.warning(
                    f"Partially loaded command module: {module_name} ({module_error_count} command registration failures)"
                )

        except Exception as e:
            error_modules.append(module_name)
            logger.error(f"Failed to load command module '{module_name}': {e}")

    if "identity" not in loaded_modules:
        try:
            from openminion.modules.identity.controlplane.main import command_module

            module_instance = command_module()
            for spec in module_instance.get_commands():
                registry.register_command_spec(spec)
            loaded_modules.append("identity")
            error_modules = [
                item
                for item in error_modules
                if item != "identity" and not str(item).startswith("identity(")
            ]
            logger.info("Loaded command module via fallback import: identity")
        except Exception as e:
            if "identity" not in error_modules:
                error_modules.append("identity")
            logger.error(f"Failed to load fallback command module 'identity': {e}")

    return loaded_modules, error_modules


def get_discovered_module_metadata() -> list[dict[str, str]]:
    """Get metadata about discovered command modules without loading them."""
    try:
        eps = importlib.metadata.entry_points()
    except Exception:
        return []

    command_eps = _select_command_entry_points(eps)

    modules: list[dict[str, str]] = []
    for ep in command_eps:
        distribution = "unknown"
        version = "unknown"
        try:
            module_metadata = importlib.metadata.metadata(ep.module)
            distribution = module_metadata.get("Name", "unknown")
            version = module_metadata.get("Version", "unknown")
        except Exception:
            pass
        modules.append(
            {
                "name": ep.name,
                "module": ep.module,
                "attr": ".".join(ep.attr.split(".")[:-1]) if ep.attr else "",
                "distribution": distribution,
                "version": version,
                "entry_point": str(ep),
            }
        )

    return modules
