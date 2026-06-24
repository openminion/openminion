import pytest
from openminion.tools.weather.providers.openmeteo.interfaces import (
    CONTRACT_VERSION,
    WeatherRequestEnvelope,
    WeatherResultEnvelope,
    WeatherErrorEnvelope,
    validate_contract_version,
    is_compatible,
)
from openminion.modules.tool import (
    PLUGIN_CONTRACT_VERSION,
    ContractValidator,
    ToolRequestEnvelope,
    ToolResultEnvelope,
    ToolErrorEnvelope,
)


def test_weather_plugin_interface_baseline():
    assert CONTRACT_VERSION == PLUGIN_CONTRACT_VERSION
    assert CONTRACT_VERSION == "v1"


def test_weather_plugin_inherits_base_validators():
    assert validate_contract_version("v1") is True
    assert validate_contract_version("v1.0") is True
    assert validate_contract_version("v2") is True
    assert validate_contract_version("invalid") is False

    assert is_compatible("v1", CONTRACT_VERSION) is True
    assert ContractValidator.is_compatible("v1.1", CONTRACT_VERSION) is True


def test_weather_request_envelope_inheritance():
    req = WeatherRequestEnvelope(
        method="weather.current",
        args={"location": "San Francisco"},
        contract_version="v1",
    )

    assert req.method == "weather.current"
    assert req.args == {"location": "San Francisco"}
    assert req.contract_version == "v1"

    base_req = ToolRequestEnvelope(
        method="weather.current",
        args={"location": "San Francisco"},
        contract_version="v1",
    )

    assert req.method == base_req.method
    assert req.args == base_req.args


def test_weather_result_envelope_inheritance():
    result = WeatherResultEnvelope(
        status="ok",
        data={"temperature": 22.5, "location": "San Francisco"},
        artifacts={"weather_data": "ref1"},
        contract_version="v1.0",
    )

    assert result.status == "ok"
    assert result.data == {"temperature": 22.5, "location": "San Francisco"}
    assert result.artifacts == {"weather_data": "ref1"}
    assert result.contract_version == "v1.0"

    base_result = ToolResultEnvelope(
        status="ok",
        data={"temperature": 22.5, "location": "San Francisco"},
        artifacts={"weather_data": "ref1"},
        contract_version="v1.0",
    )

    assert result.status == base_result.status
    assert result.data == base_result.data
    assert result.artifacts == base_result.artifacts


def test_weather_error_envelope_inheritance():
    error = WeatherErrorEnvelope(
        error_code="LOCATION_NOT_FOUND",
        error_message="Weather location could not be resolved",
        details={"location": "Unknown City"},
        contract_version="v1.2",
    )

    assert error.error_code == "LOCATION_NOT_FOUND"
    assert error.error_message == "Weather location could not be resolved"
    assert error.details == {"location": "Unknown City"}
    assert error.contract_version == "v1.2"

    base_error = ToolErrorEnvelope(
        error_code="LOCATION_NOT_FOUND",
        error_message="Weather location could not be resolved",
        details={"location": "Unknown City"},
        contract_version="v1.2",
    )

    assert error.error_code == base_error.error_code
    assert error.error_message == base_error.error_message


def test_normalized_output_for_alias_compatibility():
    canonical_result = WeatherResultEnvelope(
        status="ok",
        data={"temperature": 25.0, "location": "New York", "humidity": 65},
        artifacts={"raw_response": "path:/weather/canonical.json"},
        contract_version=CONTRACT_VERSION,
    )

    alias_result = WeatherResultEnvelope(
        status="ok",
        data={"temperature": 18.0, "location": "London", "humidity": 70},
        artifacts={"raw_response": "path:/weather/alias.json"},
        contract_version=CONTRACT_VERSION,
    )

    assert hasattr(canonical_result, "status")
    assert hasattr(canonical_result, "data")
    assert hasattr(canonical_result, "artifacts")
    assert hasattr(canonical_result, "contract_version")

    assert hasattr(alias_result, "status")
    assert hasattr(alias_result, "data")
    assert hasattr(alias_result, "artifacts")
    assert hasattr(alias_result, "contract_version")

    assert ContractValidator.is_compatible(
        canonical_result.contract_version, PLUGIN_CONTRACT_VERSION
    )
    assert ContractValidator.is_compatible(
        alias_result.contract_version, PLUGIN_CONTRACT_VERSION
    )


def test_positive_and_negative_contract_tests():
    valid_weather_req = WeatherRequestEnvelope(
        method="weather.current",
        args={"location": "Paris"},
        contract_version=CONTRACT_VERSION,
    )
    valid_result = WeatherResultEnvelope(
        status="ok",
        data={"temperature": 10, "conditions": "Cloudy"},
        contract_version=CONTRACT_VERSION,
    )

    assert ContractValidator.validate_contract_version(
        valid_weather_req.contract_version
    )
    assert ContractValidator.validate_contract_version(valid_result.contract_version)

    is_compat = ContractValidator.is_compatible(
        valid_weather_req.contract_version, valid_result.contract_version
    )
    assert is_compat is True

    with pytest.raises(ValueError):
        WeatherRequestEnvelope(
            method="weather.current",
            args={"location": "Tokyo"},
            contract_version="invalid-version",
        )

    with pytest.raises(ValueError):
        WeatherResultEnvelope(
            status="error",
            data={},
            contract_version="bad-format",
        )


def test_smoke_command_simulation():
    weather_request = WeatherRequestEnvelope(
        method="weather.current",
        args={"location": "Seattle"},
        contract_version=PLUGIN_CONTRACT_VERSION,
    )

    weather_result = WeatherResultEnvelope(
        status="ok",
        data={
            "location": {"name": "Seattle", "country": "US"},
            "temperature_c": 15.5,
            "condition": "Partly Cloudy",
        },
        contract_version=PLUGIN_CONTRACT_VERSION,
    )

    assert weather_request.contract_version == weather_result.contract_version
    assert ContractValidator.is_compatible(
        weather_request.contract_version, weather_result.contract_version
    )
