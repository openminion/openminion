# ruff: noqa: E402

import logging
from pathlib import Path
import sys

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOPHIAGRAPH_SRC = _REPO_ROOT / "sophiagraph" / "src"
if _SOPHIAGRAPH_SRC.exists():
    sys.path.insert(0, str(_SOPHIAGRAPH_SRC))

from openminion.base.config import ConfigManager, OpenMinionConfig
from openminion.services.bootstrap.config import bootstrap_config_manager


@pytest.fixture(autouse=True)
def _restore_logging_state():
    """Prevent logging configuration tests from contaminating later tests."""
    root = logging.getLogger()
    root_handlers = list(root.handlers)
    root_filters = list(root.filters)
    root_level = root.level
    manager_disable = logging.root.manager.disable
    handler_states = {
        handler: (handler.level, handler.formatter, list(handler.filters))
        for handler in root_handlers
    }
    logger_states = {
        name: (
            logger.level,
            logger.disabled,
            logger.propagate,
            list(logger.handlers),
            list(logger.filters),
        )
        for name, logger in logging.root.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }

    yield

    logging.disable(manager_disable)
    for handler in list(root.handlers):
        if handler not in root_handlers:
            root.removeHandler(handler)
            handler.close()
    for handler in root_handlers:
        if handler not in root.handlers:
            root.addHandler(handler)
        level, formatter, filters = handler_states[handler]
        handler.setLevel(level)
        handler.setFormatter(formatter)
        handler.filters[:] = filters
    root.setLevel(root_level)
    root.filters[:] = root_filters

    for name, state in logger_states.items():
        logger = logging.getLogger(name)
        level, disabled, propagate, handlers, filters = state
        logger.setLevel(level)
        logger.disabled = disabled
        logger.propagate = propagate
        logger.handlers[:] = handlers
        logger.filters[:] = filters

    for name, logger in logging.root.manager.loggerDict.items():
        if name in logger_states or not isinstance(logger, logging.Logger):
            continue
        logger.setLevel(logging.NOTSET)
        logger.disabled = False
        logger.propagate = True
        logger.handlers.clear()
        logger.filters.clear()


@pytest.fixture
def fresh_config_manager(tmp_path):
    manager = ConfigManager(
        base_config=OpenMinionConfig(),
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
        config_path=tmp_path / "config.json",
    )
    bootstrap_config_manager(manager)
    return manager


@pytest.fixture(autouse=True)
def _force_isolated_test_roots(monkeypatch, tmp_path):
    """Point ordinary tests at temporary OpenMinion roots."""
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    # Allow tmp_path-backed databases inside isolated test roots.
    monkeypatch.setenv("OPENMINION_DATA_ROOT_ENFORCEMENT", "soft")
