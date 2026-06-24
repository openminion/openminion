from __future__ import annotations

import importlib
import sys

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

for _public_name, _canonical_name in _PUBLIC_MODULES.items():
    globals()[_public_name] = importlib.import_module(_canonical_name)

for _legacy_name, _canonical_name in _COMPAT_MODULE_ALIASES.items():
    _module = importlib.import_module(_canonical_name)
    globals()[_legacy_name] = _module
    sys.modules[f"{__name__}.{_legacy_name}"] = _module
