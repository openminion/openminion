import pytest
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError

from openminion.modules.tool.runtime.argument_repair import (
    missing_simple_required_fields,
    synthesize_simple_tool_arguments,
    validate_model_tool_args,
)


def test_web_search_missing_required_fields_accepts_q_alias() -> None:
    missing = missing_simple_required_fields(
        tool_name="web.search",
        arguments={"q": "latest Iran news 2025"},
    )
    assert missing == ()


def test_web_search_synthesizes_query_from_q_alias_and_count_alias() -> None:
    repaired = synthesize_simple_tool_arguments(
        tool_name="web.search",
        user_input="ignored because q already exists",
        existing_args={"q": "latest Iran news 2025", "count": 10},
    )
    assert repaired == {
        "q": "latest Iran news 2025",
        "count": 10,
        "query": "latest Iran news 2025",
        "max_results": 10,
    }


def test_weather_location_is_optional_for_current_location_lookup() -> None:
    assert missing_simple_required_fields(tool_name="weather", arguments={}) == ()
    assert (
        synthesize_simple_tool_arguments(
            tool_name="weather",
            user_input="what is the weather here?",
        )
        == {}
    )


def test_weather_synthesis_preserves_explicit_location() -> None:
    assert synthesize_simple_tool_arguments(
        tool_name="weather",
        user_input="what is the weather in San Francisco?",
    ) == {"location": "San Francisco"}


class _Inner(BaseModel):
    min_total: int = Field(alias="minTotal", ge=0)


class _AliasOnlyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int = Field(alias="external_value")
    inner: _Inner | None = None


class _ByNameArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    json_payload: dict | None = Field(default=None, alias="json")


def test_validate_model_tool_args_keeps_alias_keys_for_alias_only_models() -> None:
    validated = validate_model_tool_args(
        {"external_value": 3, "inner": {"minTotal": 7}}, _AliasOnlyArgs, "t"
    )

    assert validated == {"external_value": 3, "inner": {"minTotal": 7}}
    assert _AliasOnlyArgs.model_validate(validated).inner == _Inner(minTotal=7)


def test_validate_model_tool_args_rejects_field_names_for_alias_only_models() -> None:
    with pytest.raises(ValidationError):
        validate_model_tool_args({"value": 3}, _AliasOnlyArgs, "t")
    with pytest.raises(ValidationError):
        validate_model_tool_args(
            {"external_value": 3, "inner": {"minTotal": -1}}, _AliasOnlyArgs, "t"
        )


def test_validate_model_tool_args_accepts_both_keys_for_populate_by_name() -> None:
    for key in ("json", "json_payload"):
        assert validate_model_tool_args({key: {"a": 1}}, _ByNameArgs, "t") == {
            "json_payload": {"a": 1}
        }


class _SplitAliasInner(BaseModel):
    value: int = Field(validation_alias="in_value", serialization_alias="out_value")


class _SplitAliasArgs(BaseModel):
    top: int = Field(serialization_alias="output_top")
    inner: _SplitAliasInner


def test_validate_model_tool_args_never_uses_serialization_alias_keys():
    out = validate_model_tool_args(
        {"top": 1, "inner": {"in_value": 2}}, _SplitAliasArgs, "probe"
    )

    assert out == {"top": 1, "inner": {"in_value": 2}}
    assert _SplitAliasArgs.model_validate(out).inner.value == 2


def test_validate_model_tool_args_keeps_canonical_dump_when_it_revalidates() -> None:
    class _Plain(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: int
        label: str = "x"

    result = validate_model_tool_args({"value": "5"}, _Plain, "t")
    assert result == {"value": 5, "label": "x"}


def test_validate_model_tool_args_keeps_root_model_shapes() -> None:
    class _Numbers(RootModel[list[int]]):
        pass

    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

        query: _Numbers

    assert validate_model_tool_args({"query": [1, 2]}, _Args, "t") == {"query": [1, 2]}


def test_tool_args_preserves_nested_serialize_by_alias_input_keys() -> None:
    if "serialize_by_alias" not in ConfigDict.__annotations__:
        return
    class _Query(BaseModel):
        model_config = ConfigDict(serialize_by_alias=True, extra="forbid")

        value: int = Field(serialization_alias="output_value")

    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

        query: _Query

    provider = {"query": {"value": 7}}
    result = validate_model_tool_args(provider, _Args, "serialize-by-alias")

    assert result == provider
    assert _Args.model_validate(result).query.value == 7


def test_transforming_validator_runs_once_in_argument_owner() -> None:
    from pydantic import field_validator

    calls: list[int] = []

    class _Args(BaseModel):
        values: list[int]

        @field_validator("values", mode="before")
        @classmethod
        def _append_marker(cls, value: list[int]) -> list[int]:
            calls.append(1)
            value.append(99)
            return value

    provider = {"values": [1]}
    out = validate_model_tool_args(provider, _Args, "probe")

    assert out == {"values": [1, 99]}
    assert provider == {"values": [1, 99]}
    assert len(calls) == 1


def test_validate_model_tool_args_hands_on_repaired_mapping_not_provider_payload() -> (
    None
):
    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: int = Field(alias="external")

    # Provider sent a field-name key an alias-only model rejects: no bypass.
    with pytest.raises(ValidationError):
        validate_model_tool_args({"value": 7}, _Args, "t")


def test_validate_model_tool_args_preserves_nested_alias_keys() -> None:
    class _NestedAlias(BaseModel):
        value: int = Field(alias="external_value")

    class _Args(BaseModel):
        query: _NestedAlias

    result = validate_model_tool_args(
        {"query": {"external_value": 5}}, _Args, "nested-alias"
    )

    assert result == {"query": {"external_value": 5}}
    assert _Args.model_validate(result).query.value == 5
