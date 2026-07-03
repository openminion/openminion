import sys
from importlib import import_module
from typing import Any

_PUBLIC_MODULES = {
    "parser": "openminion.cli.parser",
}
_COMPAT_MODULE_ALIASES = {
    "config_helpers": "openminion.cli.bootstrap.loader",
    "paths": "openminion.cli.bootstrap.paths",
    "parser_helpers": "openminion.cli.parser.flags",
    "contracts": "openminion.cli.parser.contracts",
    "identity_provenance": "openminion.cli.identity.provenance",
    "identity_sync": "openminion.cli.identity.sync",
    "daemon_client": "openminion.cli.transport.daemon_client",
    "styles": "openminion.cli.presentation.styles",
}

__all__ = tuple(sorted((*_PUBLIC_MODULES, *_COMPAT_MODULE_ALIASES)))


def __getattr__(name: str) -> Any:
    module_name = _PUBLIC_MODULES.get(name) or _COMPAT_MODULE_ALIASES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name)
    globals()[name] = module
    if name in _COMPAT_MODULE_ALIASES:
        sys.modules[f"{__name__}.{name}"] = module
    return module
