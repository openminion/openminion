from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import BaseModel, Field, ValidationError

from openminion.modules.tool.framework import (
    ToolDecl,
    ToolFamilySpec,
    derive_tool_specs,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools import tool
from openminion.tools.decorator import _MODEL_VALIDATE_ACCEPTS_BY_NAME


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


def test_decorator_handler_supports_positional_only_parameters() -> None:
    @tool
    def add(a: int, b: int, /) -> int:
        return a + b

    decl = add.tool_decl
    assert decl.handler(decl.args_model(a=2, b=3)) == 5
    assert decl.handler({"a": 4, "b": 5}) == 9


def test_decorator_handler_supports_mixed_positional_only_parameters() -> None:
    @tool
    def mix(a: int, /, b: int, *, c: int = 3) -> tuple[int, int, int]:
        return a, b, c

    decl = mix.tool_decl
    assert decl.handler(decl.args_model(a=1, b=2, c=7)) == (1, 2, 7)
    assert decl.handler({"a": 1, "b": 2}) == (1, 2, 3)


def test_decorator_tool_family_spec_wraps_decl() -> None:
    @tool
    def ping() -> str:
        return "pong"

    spec = ping.tool_family_spec()
    assert isinstance(spec, ToolFamilySpec)
    assert spec.module_id == "openminion.tools.user.ping"
    assert len(spec.tools) == 1
    assert spec.tools[0] is ping.tool_decl


def test_decorator_tool_family_spec_exposes_tool_by_default() -> None:
    @tool
    def ping() -> str:
        return "pong"

    (profile,) = ping.tool_family_spec().exposure_profiles
    assert profile.tool_names == frozenset({"ping"})
    assert profile.default_active is True


def test_decorator_profile_ids_keep_hyphen_and_underscore_names_distinct() -> None:
    from openminion.modules.tool.exposure import ToolExposureService

    @tool(name="lookup-order")
    def lookup_hyphen() -> str:
        return "a"

    @tool(name="lookup_order")
    def lookup_underscore() -> str:
        return "b"

    (hyphen,) = lookup_hyphen.tool_family_spec().exposure_profiles
    (underscore,) = lookup_underscore.tool_family_spec().exposure_profiles
    assert hyphen.profile_id != underscore.profile_id

    service = ToolExposureService()
    service.register_profiles((hyphen,))
    service.register_profiles((underscore,))
    assert {p.profile_id for p in service.profiles} >= {
        hyphen.profile_id,
        underscore.profile_id,
    }


def test_decorator_description_reaches_provider_schema() -> None:
    @tool
    def ping() -> str:
        """Check whether the service is available."""

        return "pong"

    registry = ToolRegistry()
    registry.add(derive_tool_specs(ping.tool_family_spec())[0])

    assert registry.provider_specs()[0].description == (
        "Check whether the service is available."
    )


def test_decorator_default_arguments_become_optional_fields() -> None:
    @tool
    def fetch(url: str, timeout: int = 30) -> str:
        return f"{url}::{timeout}"

    args_model = fetch.tool_args_model
    instance = args_model(url="https://example.com")
    assert instance.timeout == 30


def test_decorator_preserves_annotated_field_default_without_python_default() -> None:
    @tool
    def a(x: Annotated[int, Field(default=5)]) -> int:
        return x

    @tool
    def b(x: Annotated[list[int], Field(default_factory=list)]) -> list[int]:
        return x

    a_field = a.tool_args_model.model_fields["x"]
    assert a_field.default == 5
    assert not a_field.is_required()
    assert a.tool_args_model().x == 5
    assert a.tool_args_model(x=9).x == 9

    b_field = b.tool_args_model.model_fields["x"]
    assert b_field.default_factory is list
    assert not b_field.is_required()
    assert b.tool_args_model().x == []
    assert b.tool_args_model(x=[1]).x == [1]


def test_decorator_python_default_precedes_annotated_field_default() -> None:
    @tool
    def pick(x: Annotated[int, Field(default=5, ge=1)] = 7) -> int:
        return x

    field = pick.tool_args_model.model_fields["x"]
    assert field.default == 7
    assert not field.is_required()
    assert pick.tool_args_model().x == 7
    with pytest.raises(ValidationError):
        pick.tool_args_model(x=0)


def test_decorator_field_default_alias_and_constraints_reach_handler() -> None:
    @tool
    def scalar(x: int = Field(default=7, alias="external", ge=1)) -> int:
        return x

    field = scalar.tool_args_model.model_fields["x"]
    assert field.default == 7
    assert not field.is_required()
    assert field.alias == "external"
    assert scalar.tool_decl.handler({}) == 7
    assert scalar.tool_decl.handler({"external": 9}) == 9
    with pytest.raises(ValidationError):
        scalar.tool_decl.handler({"x": -1})
    with pytest.raises(ValidationError):
        scalar.tool_decl.handler({"external": 0})


def test_decorator_field_default_factory_reaches_handler() -> None:
    @tool
    def factory(x: list[int] = Field(default_factory=list)) -> list[int]:
        return x

    field = factory.tool_args_model.model_fields["x"]
    assert field.default_factory is list
    assert not field.is_required()
    assert factory.tool_decl.handler({}) == []
    assert factory.tool_decl.handler({"x": [1, 2]}) == [1, 2]


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


@pytest.mark.parametrize("named", [False, True])
def test_functional_decorator_preserves_options_and_identity(named) -> None:
    def lookup(value: str) -> str:
        return value

    options = dict(
        min_scope="POWER_USER",
        dangerous=True,
        idempotent=True,
        tags=("external",),
        capabilities=("lookup",),
    )
    if named:
        options.update(name="custom.lookup", description="Lookup value.")
    decorated = tool(lookup, **options)
    assert decorated is lookup
    assert decorated("marker") == "marker"
    spec = derive_tool_specs(decorated.tool_family_spec())[0]
    assert spec.min_scope == "POWER_USER"
    assert spec.dangerous is True
    assert spec.idempotent is True
    assert "external" in spec.tags
    assert "lookup" in spec.capabilities
    if named:
        assert spec.name == "custom.lookup"
        assert spec.description == "Lookup value."


def test_decorator_resolves_postponed_annotated_field_metadata() -> None:
    from tests.tools._postponed_annotation_tools import lookback

    schema = lookback.tool_args_model.model_json_schema()
    days = schema["properties"]["days"]
    assert days["description"] == "Look-back days"
    assert days["minimum"] == 1
    assert days["default"] == 2
    assert lookback.tool_args_model(days=3).days == 3
    with pytest.raises(ValidationError):
        lookback.tool_args_model(days=0)
    with pytest.raises(ValidationError):
        lookback.tool_args_model(days=3, unexpected=1)


def test_decorator_resolves_postponed_module_level_custom_types() -> None:
    from tests.tools._postponed_annotation_tools import Range, Window, summarize

    args_model = summarize.tool_args_model
    schema = args_model.model_json_schema()
    assert set(schema["required"]) == {"window", "span", "label"}
    assert schema["properties"]["limit"]["default"] == 5
    args = args_model(window="week", span={"start": 1, "end": 4}, label=object())
    assert args.window is Window.WEEK
    assert args.span == Range(start=1, end=4)
    with pytest.raises(ValidationError):
        args_model(window="month", span={"start": 1, "end": 4}, label="x")


def test_decorator_rejects_unresolvable_postponed_annotation(
    tmp_path, monkeypatch
) -> None:
    import importlib

    (tmp_path / "unresolved_tool_module.py").write_text(
        "from __future__ import annotations\n"
        "from openminion.tools import tool\n"
        "@tool\n"
        "def broken(value: MissingType) -> int:\n"
        "    return 1\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(TypeError, match="MissingType"):
        importlib.import_module("unresolved_tool_module")


def _import_postponed_module(tmp_path, monkeypatch, name: str, body: str):
    import importlib

    (tmp_path / f"{name}.py").write_text(
        "from __future__ import annotations\n"
        "from openminion.tools import tool\n" + body,
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return importlib.import_module(name)


def _assert_value_only_schema(decorated) -> None:
    args_model = decorated.tool_args_model
    schema = args_model.model_json_schema()
    assert set(schema["properties"]) == {"value"}
    assert args_model().value == 2
    with pytest.raises(ValidationError):
        args_model(value=2, unexpected=1)


def test_decorator_ignores_unresolved_postponed_return_annotation(
    tmp_path, monkeypatch
) -> None:
    module = _import_postponed_module(
        tmp_path,
        monkeypatch,
        "postponed_return_only_module",
        "@tool\ndef lookup(value: int = 2) -> ResultOnlyForTyping:\n    return value\n",
    )
    _assert_value_only_schema(module.lookup)


def test_decorator_ignores_unresolved_postponed_variadic_annotations(
    tmp_path, monkeypatch
) -> None:
    module = _import_postponed_module(
        tmp_path,
        monkeypatch,
        "postponed_variadic_module",
        "@tool\n"
        "def lookup(\n"
        "    value: int = 2, *args: ArgsOnlyForTyping, **kwargs: ExtraOnlyForTyping\n"
        ") -> int:\n"
        "    return value\n",
    )
    _assert_value_only_schema(module.lookup)


def test_decorator_ignores_postponed_self_and_cls_annotations(
    tmp_path, monkeypatch
) -> None:
    module = _import_postponed_module(
        tmp_path,
        monkeypatch,
        "postponed_self_cls_module",
        "class Client:\n"
        "    @tool\n"
        "    def lookup(self: Client, value: int = 2) -> int:\n"
        "        return value\n"
        "\n"
        "    @tool\n"
        "    def lookup_cls(cls: type[Client], value: int = 2) -> int:\n"
        "        return value\n",
    )
    _assert_value_only_schema(module.Client.lookup)
    _assert_value_only_schema(module.Client.lookup_cls)


def test_decorator_still_rejects_unresolved_exposed_argument_with_valid_return(
    tmp_path, monkeypatch
) -> None:
    with pytest.raises(TypeError, match="MissingArgType"):
        _import_postponed_module(
            tmp_path,
            monkeypatch,
            "postponed_missing_arg_module",
            "@tool\n"
            "def lookup(value: MissingArgType, **kwargs: int) -> int:\n"
            "    return 1\n",
        )


def test_decorator_handler_passes_model_and_dataclass_args_as_instances() -> None:
    from tests.tools._postponed_annotation_tools import summarize, summarize_span

    decl = summarize.tool_decl
    validated = decl.args_model(window="week", span={"start": 1, "end": 4}, label="x")
    # The validated args model, the dumped mapping the brain runtime passes,
    # and the raw mapping the MCP server passes all reach the function with a
    # ``Range`` instance for ``span`` (``span.start`` fails on a dict).
    assert decl.handler(validated) == "week:1-4:x:5"
    assert decl.handler(validated.model_dump()) == "week:1-4:x:5"
    raw = {"window": "week", "span": {"start": 1, "end": 4}, "label": "x"}
    assert decl.handler(raw) == "week:1-4:x:5"
    assert (
        summarize_span.tool_decl.handler({"span": {"start": 2, "end": 3}}) == "Span:2-3"
    )


def test_decorator_handler_accepts_alias_and_canonical_mappings() -> None:
    @tool
    def show(value: Annotated[int, Field(alias="external_value", ge=0)]) -> str:
        """Show value."""
        return str(value)

    validated = show.tool_args_model.model_validate({"external_value": 3})
    assert show.tool_decl.handler(validated) == "3"
    assert show.tool_decl.handler(validated.model_dump()) == "3"
    assert show.tool_decl.handler(validated.model_dump(by_alias=True)) == "3"
    assert show.tool_decl.handler({"external_value": 4}) == "4"
    assert show.tool_decl.handler({"value": 5}) == "5"
    # The args model the runtime validates model output with is unchanged.
    with pytest.raises(ValidationError):
        show.tool_args_model.model_validate({"value": 3})
    with pytest.raises(ValidationError):
        show.tool_decl.handler({"value": -1})
    with pytest.raises(ValidationError):
        show.tool_decl.handler({"value": 1, "other": 2})
    with pytest.raises(ValidationError):
        show.tool_decl.handler({})


def test_decorator_handler_rejects_invalid_structured_arguments() -> None:
    from tests.tools._postponed_annotation_tools import summarize, summarize_span

    with pytest.raises(ValidationError):
        summarize.tool_decl.handler(
            {"window": "week", "span": {"start": "x", "end": 4}, "label": "x"}
        )
    with pytest.raises(ValidationError):
        summarize_span.tool_decl.handler({"span": {"start": 1}})


class _AliasedInner(BaseModel):
    value: int = Field(alias="external", ge=0)
    note: str = "none"


def test_decorator_handler_accepts_nested_alias_model_mappings() -> None:
    @tool
    def report(inner: _AliasedInner, label: str = "all") -> str:
        """Report inner."""
        assert type(inner) is _AliasedInner
        return f"{inner.value}:{inner.note}:{label}"

    validated = report.tool_args_model.model_validate({"inner": {"external": 7}})
    assert report.tool_decl.handler(validated) == "7:none:all"
    assert report.tool_decl.handler(validated.model_dump(by_alias=True)) == "7:none:all"
    # The runtime passes the canonical (field-name) dump of the args model.
    # Pydantic before 2.11 cannot validate a nested model's field names
    # without changing that model's own configuration (#436 review).
    if _MODEL_VALIDATE_ACCEPTS_BY_NAME:
        assert report.tool_decl.handler(validated.model_dump()) == "7:none:all"
    else:
        with pytest.raises(ValidationError):
            report.tool_decl.handler(validated.model_dump())
    # Validation, extra-key and default controls on the nested model.
    with pytest.raises(ValidationError):
        report.tool_decl.handler({"inner": {"external": -1}})
    with pytest.raises(ValidationError):
        report.tool_decl.handler({"inner": {"external": 1}, "other": 2})
    with pytest.raises(ValidationError):
        report.tool_decl.handler({"inner": {}})
    assert report.tool_decl.handler({"inner": {"external": 2}, "label": "x"}) == (
        "2:none:x"
    )
