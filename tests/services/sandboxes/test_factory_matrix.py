from __future__ import annotations

import pytest

from openminion.services.runtime.sandboxes import (
    E2BSandboxAdapter,
    ModalSandboxAdapter,
    PyodideSandboxAdapter,
    SandboxExecResult,
    build_sandbox_adapter,
)


def test_service_sandbox_contract_is_canonical_module_owner() -> None:
    from openminion.modules.runtime.sandboxes import SandboxExecResult as canonical
    from openminion.services.runtime.sandboxes import (
        SandboxExecResult as compatibility,
    )

    assert compatibility is canonical


def test_factory_dispatches_to_each_provider() -> None:
    assert isinstance(build_sandbox_adapter("e2b"), E2BSandboxAdapter)
    assert isinstance(build_sandbox_adapter("modal"), ModalSandboxAdapter)
    assert isinstance(build_sandbox_adapter("pyodide"), PyodideSandboxAdapter)


def test_factory_rejects_unknown_spec() -> None:
    with pytest.raises(ValueError, match="unknown sandbox spec"):
        build_sandbox_adapter("does-not-exist")


def test_factory_normalises_case() -> None:
    assert isinstance(build_sandbox_adapter("E2B"), E2BSandboxAdapter)
    assert isinstance(build_sandbox_adapter("  pyodide  "), PyodideSandboxAdapter)


def test_e2b_missing_api_key_raises_helpful_error() -> None:
    adapter = E2BSandboxAdapter(api_key="")
    with pytest.raises(RuntimeError, match="E2B_API_KEY"):
        adapter.exec(["echo", "hi"])


def test_pyodide_missing_deno_raises_helpful_error() -> None:
    adapter = PyodideSandboxAdapter(deno_path=None)
    with pytest.raises(RuntimeError, match="deno"):
        adapter.exec(["python", "-c", "print('hi')"])


def test_pyodide_rejects_non_python_invocations() -> None:
    adapter = PyodideSandboxAdapter(deno_path="/fake/path")
    with pytest.raises(ValueError, match="python"):
        adapter.exec(["bash", "-c", "echo hi"])


def test_sandbox_exec_result_defaults_meta_to_dict() -> None:
    result = SandboxExecResult(exit_code=0, stdout="", stderr="")
    assert result.meta == {}


def test_provider_adapters_expose_name_attribute() -> None:
    assert E2BSandboxAdapter().name == "e2b"
    assert ModalSandboxAdapter().name == "modal"
    assert PyodideSandboxAdapter().name == "pyodide"
