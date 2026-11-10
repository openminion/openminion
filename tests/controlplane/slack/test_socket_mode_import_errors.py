"""Slack Socket Mode tells an absent extra apart from an import-time fault.

Every control drives ``build_slack_runner`` so the SDK import happens on the
path the product uses, performed by the real import machinery rather than by a
finder we install to raise for us. The faulting SDKs are real packages on
``sys.path`` whose loader runs, so the product sees the exception the import
actually raised; the single exception is the stale non-package control, which
deliberately fabricates a ``ModuleType`` to show that a leftover entry is not
evidence of absence.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from openminion.modules.controlplane.channels.slack.adapter import build_slack_runner
from openminion.modules.controlplane.channels.slack.config import SlackChannelConfig
from openminion.modules.controlplane.channels.slack.constants import MODE_SOCKET
from openminion.modules.controlplane.channels.slack.socket_mode import (
    MissingSlackSocketDependency,
)

PROBE_MODULE = "slack_fault_probe"

_PROBE_SOURCE = """\
IMPORT_FAULT = ImportError("slack_sdk initialisation exploded")
ROOT_FAULT = ModuleNotFoundError("No module named 'slack_sdk'", name="slack_sdk")
SUBMODULE_FAULT = ModuleNotFoundError(
    "No module named 'slack_sdk.socket_mode'", name="slack_sdk.socket_mode"
)
RUNTIME_FAULT = RuntimeError("socket-mode import exploded")
RECORDED = []
SEEN = []
"""


def _is_control_module(name: str) -> bool:
    """True for every module the imports here can synthesise or shadow."""
    return name == "slack_sdk" or name.startswith("slack_sdk.") or name == PROBE_MODULE


@pytest.fixture
def sdk_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isolate imports: tmp_path first, and leave ``sys.modules`` as we found it.

    The snapshot is taken before any control imports anything, so teardown can
    drop every synthetic ``slack_sdk*``/probe module the controls introduced
    while putting back any entry the surrounding process already had loaded.
    """
    original = {
        name: module for name, module in sys.modules.items() if _is_control_module(name)
    }
    installed = [entry for entry in sys.path if (Path(entry) / "slack_sdk").exists()]
    for name in original:
        del sys.modules[name]
    monkeypatch.setattr(
        sys, "path", [str(tmp_path), *[e for e in sys.path if e not in installed]]
    )
    try:
        yield tmp_path
    finally:
        for name in [n for n in sys.modules if _is_control_module(n)]:
            del sys.modules[name]
        sys.modules.update(original)
        importlib.invalidate_caches()


def _probe(root: Path) -> ModuleType:
    (root / f"{PROBE_MODULE}.py").write_text(_PROBE_SOURCE, encoding="utf-8")
    importlib.invalidate_caches()
    return importlib.import_module(PROBE_MODULE)


def _fake_sdk(root: Path, init_source: str) -> Path:
    package = root / "slack_sdk"
    package.mkdir()
    (package / "__init__.py").write_text(init_source, encoding="utf-8")
    importlib.invalidate_caches()
    return package


def _build() -> object:
    return build_slack_runner(
        config=SlackChannelConfig(
            mode=MODE_SOCKET, app_token="xapp-1", bot_token="xoxb-1"
        ),
        runtime=object(),
        delivery=object(),
    )


def test_a_genuinely_missing_sdk_keeps_the_install_guidance(sdk_root: Path) -> None:
    assert importlib.util.find_spec("slack_sdk") is None

    with pytest.raises(MissingSlackSocketDependency) as excinfo:
        _build()

    assert "openminion[slack]" in str(excinfo.value)
    cause = excinfo.value.__cause__
    assert isinstance(cause, ModuleNotFoundError)
    assert cause.name == "slack_sdk"


def test_an_unrelated_fault_inside_an_installed_sdk_propagates(sdk_root: Path) -> None:
    probe = _probe(sdk_root)
    _fake_sdk(
        sdk_root, "from slack_fault_probe import RUNTIME_FAULT\nraise RUNTIME_FAULT\n"
    )

    with pytest.raises(RuntimeError) as excinfo:
        _build()

    assert excinfo.value is probe.RUNTIME_FAULT


def test_an_import_error_raised_by_the_installed_sdk_propagates(
    sdk_root: Path,
) -> None:
    probe = _probe(sdk_root)
    _fake_sdk(
        sdk_root, "from slack_fault_probe import IMPORT_FAULT\nraise IMPORT_FAULT\n"
    )

    with pytest.raises(ImportError) as excinfo:
        _build()

    assert excinfo.value is probe.IMPORT_FAULT
    assert not isinstance(excinfo.value, MissingSlackSocketDependency)


def test_a_dependency_missing_inside_the_installed_sdk_propagates(
    sdk_root: Path,
) -> None:
    probe = _probe(sdk_root)
    _fake_sdk(
        sdk_root,
        "from slack_fault_probe import RECORDED\n"
        "try:\n"
        "    import slack_sdk_helper_dependency\n"
        "except ModuleNotFoundError as exc:\n"
        "    RECORDED.append(exc)\n"
        "    raise\n",
    )

    with pytest.raises(ModuleNotFoundError) as excinfo:
        _build()

    assert excinfo.value is probe.RECORDED[0]
    assert excinfo.value.name == "slack_sdk_helper_dependency"
    assert not isinstance(excinfo.value, MissingSlackSocketDependency)


def test_an_installed_sdk_naming_itself_is_not_absence(sdk_root: Path) -> None:
    """A root-name ModuleNotFoundError from an installed __init__ is not proof."""
    probe = _probe(sdk_root)
    _fake_sdk(sdk_root, "from slack_fault_probe import ROOT_FAULT\nraise ROOT_FAULT\n")

    with pytest.raises(ModuleNotFoundError) as excinfo:
        _build()

    assert excinfo.value is probe.ROOT_FAULT
    assert importlib.util.find_spec("slack_sdk") is not None


def test_a_faulting_submodule_of_the_installed_sdk_propagates(
    sdk_root: Path,
) -> None:
    probe = _probe(sdk_root)
    package = _fake_sdk(
        sdk_root, "from slack_fault_probe import SEEN\nSEEN.append('slack_sdk')\n"
    )
    (package / "socket_mode.py").write_text(
        "from slack_fault_probe import SEEN, SUBMODULE_FAULT\n"
        "SEEN.append('slack_sdk.socket_mode')\n"
        "raise SUBMODULE_FAULT\n",
        encoding="utf-8",
    )
    importlib.invalidate_caches()

    with pytest.raises(ModuleNotFoundError) as excinfo:
        _build()

    assert excinfo.value is probe.SUBMODULE_FAULT
    assert probe.SEEN == ["slack_sdk", "slack_sdk.socket_mode"]
    loaded = sys.modules["slack_sdk"]
    assert Path(str(loaded.__file__)).parent == package
    assert list(loaded.__path__) == [str(package)]


def test_a_non_package_sdk_entry_is_not_absence(sdk_root: Path) -> None:
    """A stale non-package entry fails differently and must not claim the hint."""
    # The fabricated entry is left for ``sdk_root`` to discard, so a stale key
    # the surrounding process really had loaded is restored instead of popped
    # by a later monkeypatch undo.
    sys.modules["slack_sdk"] = ModuleType("slack_sdk")

    with pytest.raises(ModuleNotFoundError) as excinfo:
        _build()

    assert "is not a package" in str(excinfo.value)
    assert excinfo.value.name == "slack_sdk.socket_mode"
    assert not isinstance(excinfo.value, MissingSlackSocketDependency)
