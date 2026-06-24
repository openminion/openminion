from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec
from openminion.tools import tool


def test_decorator_bare_form_preserves_callability() -> None:
    @tool
    def greet(name: str) -> str:
        return f"hello {name}"

    # The decorated callable is still the original function.
    assert greet("world") == "hello world"


def test_decorator_exposes_tool_decl_with_inferred_name_and_args() -> None:
    @tool
    def add(x: int, y: int) -> int:
        return x + y

    decl = add.tool_decl
    assert isinstance(decl, ToolDecl)
    assert decl.name == "add"
    assert decl.description == "Add two integers."
    args_model = decl.args_model
    instance = args_model(x=2, y=3)
    assert instance.x == 2 and instance.y == 3


def test_decorator_args_model_validates_types() -> None:
    @tool
    def square(x: int) -> int:
        return x * x

    with pytest.raises(ValidationError):
        square.tool_args_model(x="not-an-int")


def test_decorator_args_model_rejects_extra_fields() -> None:
    @tool
    def echo(msg: str) -> str:
        return msg

    with pytest.raises(ValidationError):
        echo.tool_args_model(msg="hi", unexpected=1)


def test_decorator_parameterized_form_overrides_name_and_description() -> None:
    @tool(name="custom.name", description="Override the description.")
    def underscore_name(x: int) -> int:
        return x

    decl = underscore_name.tool_decl
    assert decl.name == "custom.name"
    assert decl.description == "Override the description."


def test_decorator_parameterized_propagates_scope_and_flags() -> None:
    @tool(
        name="t",
        min_scope="POWER_USER",
        dangerous=True,
        idempotent=False,
        tags=("alpha",),
        capabilities=("cap1",),
    )
    def t(x: int) -> int:
        return x

    decl = t.tool_decl
    assert decl.min_scope == "POWER_USER"
    assert decl.dangerous is True
    assert "alpha" in decl.tags
    assert "cap1" in decl.capabilities


def test_decorator_handler_invokes_underlying_function() -> None:
    @tool
    def mul(x: int, y: int) -> int:
        return x * y

    decl = mul.tool_decl
    args = decl.args_model(x=3, y=4)
    assert decl.handler(args) == 12


def test_decorator_tool_family_spec_wraps_decl() -> None:
    @tool
    def ping() -> str:
        return "pong"

    spec = ping.tool_family_spec()
    assert isinstance(spec, ToolFamilySpec)
    assert spec.module_id == "openminion.tools.user.ping"
    assert len(spec.tools) == 1
    assert spec.tools[0] is ping.tool_decl


def test_decorator_default_arguments_become_optional_fields() -> None:
    @tool
    def fetch(url: str, timeout: int = 30) -> str:
        return f"{url}::{timeout}"

    args_model = fetch.tool_args_model
    instance = args_model(url="https://example.com")
    assert instance.timeout == 30


def test_decorator_handles_missing_type_hints_as_any() -> None:
    @tool
    def lazy(x):  # noqa: ANN001 — intentional: untyped param defaults to Any
        return x

    instance = lazy.tool_args_model(x={"deep": "thing"})
    assert instance.x == {"deep": "thing"}


def test_openminion_tool_is_reexported_at_package_root() -> None:
    import openminion

    @openminion.tool
    def noop() -> None:
        return None

    assert noop.tool_decl.name == "noop"
