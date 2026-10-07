from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from web3 import Web3

from openminion.base.config.env import EnvironmentConfig
from openminion.modules.tool.runtime.public_https import PublicHttpsResponse
from openminion.tools.blockchain.preparations import load_resolution_record
from openminion.tools.blockchain.resolution import (
    ResolveContractArgs,
    function_by_signature,
    load_resolution,
    revalidate_resolution,
    resolution_digest,
    resolve_contract,
    rpc_call,
    validate_resolution_record,
)

_TARGET = Web3.to_checksum_address("0x" + "12" * 20)
_IMPLEMENTATION = Web3.to_checksum_address("0x" + "34" * 20)
_GENESIS_HASH = "0x" + "11" * 32
_LATEST_HASH = "0x" + "22" * 32
_TARGET_CODE = "0x6000"
_IMPLEMENTATION_CODE = "0x6001"


def _env(tmp_path) -> EnvironmentConfig:
    return EnvironmentConfig(
        values={
            "OPENMINION_HOME": str(tmp_path),
            "OPENMINION_DATA_ROOT": str(tmp_path / ".openminion"),
        }
    )


def _metadata(address: str, code: str, *, duplicate: bool = False) -> dict[str, Any]:
    function = {
        "type": "function",
        "name": "balanceOf",
        "inputs": [
            {"name": "owner", "type": "address", "internalType": "address"}
        ],
        "outputs": [
            {"name": "balance", "type": "uint256", "internalType": "uint256"}
        ],
        "stateMutability": "view",
        "ignored": "provider-field",
    }
    return {
        "chainId": "1",
        "address": address,
        "match": "exact_match",
        "abi": [function, dict(function)] if duplicate else [function],
        "runtimeBytecode": {"onchainBytecode": code},
    }


class FakeHttps:
    def __init__(
        self,
        *,
        implementation: bool = False,
        duplicate_abi: bool = False,
        stale_recheck: bool = False,
        sourcify_status: int = 200,
    ) -> None:
        self.implementation = implementation
        self.duplicate_abi = duplicate_abi
        self.stale_recheck = stale_recheck
        self.sourcify_status = sourcify_status
        self.calls: list[dict[str, Any]] = []
        self.block_calls = 0

    def __call__(self, url: str, **kwargs: Any) -> PublicHttpsResponse:
        self.calls.append({"url": url, **kwargs})
        if url.startswith("https://sourcify.dev/"):
            address = _IMPLEMENTATION if _IMPLEMENTATION in url else _TARGET
            code = _IMPLEMENTATION_CODE if address == _IMPLEMENTATION else _TARGET_CODE
            return PublicHttpsResponse(
                status=self.sourcify_status,
                headers={},
                body=json.dumps(
                    _metadata(
                        address,
                        code,
                        duplicate=self.duplicate_abi,
                    )
                ).encode(),
            )

        request = json.loads(kwargs["body"])
        method = request["method"]
        params = request["params"]
        if method == "eth_chainId":
            result: Any = "0x1"
        elif method == "eth_getBlockByNumber" and params[0] == "0x0":
            result = {"number": "0x0", "hash": _GENESIS_HASH}
        elif method == "eth_getBlockByNumber":
            self.block_calls += 1
            block_hash = (
                "0x" + "33" * 32
                if self.stale_recheck and self.block_calls == 3
                else _LATEST_HASH
            )
            result = {"number": "0x10", "hash": block_hash}
        elif method == "eth_getCode":
            result = (
                _IMPLEMENTATION_CODE
                if params[0] == _IMPLEMENTATION
                else _TARGET_CODE
            )
        elif method == "eth_getStorageAt":
            result = (
                "0x" + "00" * 12 + _IMPLEMENTATION[2:].lower()
                if self.implementation
                else "0x" + "00" * 32
            )
        else:
            raise AssertionError((method, params))
        return PublicHttpsResponse(
            status=200,
            headers={},
            body=json.dumps(
                {"jsonrpc": "2.0", "id": request["id"], "result": result}
            ).encode(),
        )


def _args(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "rpc_url": "https://rpc.example",
        "expected_chain_id": 1,
        "expected_genesis_hash": _GENESIS_HASH,
        "contract_address": _TARGET,
        "explorer_contract_url": "https://explorer.example/address?view=contract",
        "research_source_urls": ["https://research.example/contracts?id=12"],
    }
    values.update(overrides)
    return values


def test_resolver_schema_is_closed_and_rpc_url_has_no_query() -> None:
    with pytest.raises(ValidationError):
        ResolveContractArgs.model_validate(_args(extra=True))
    with pytest.raises(ValidationError):
        ResolveContractArgs.model_validate(
            _args(rpc_url="https://rpc.example?network=mainnet")
        )
    with pytest.raises(ValidationError):
        ResolveContractArgs.model_validate(
            _args(research_source_urls=[f"https://source.example/{index}" for index in range(6)])
        )


def test_resolve_contract_verifies_and_persists_canonical_facts(tmp_path) -> None:
    transport = FakeHttps()
    context = SimpleNamespace(session_id="session-a", env=_env(tmp_path))

    result = resolve_contract(_args(), context, https_request=transport)

    assert result["ok"] is True
    record = result["data"]
    assert record["proxy_kind"] == "direct"
    assert record["rpc_origin"] == "https://rpc.example"
    assert record["sourcify_origin"] == "https://sourcify.dev"
    assert "rpc_url" not in record
    assert "sourcify_target_url" not in record
    assert record["abi_address"] == _TARGET
    assert record["function_abi"] == [
        {
            "type": "function",
            "name": "balanceOf",
            "inputs": [{"name": "owner", "type": "address", "components": None}],
            "outputs": [
                {"name": "balance", "type": "uint256", "components": None}
            ],
            "stateMutability": "view",
        }
    ]
    assert [json.loads(call["body"])["method"] for call in transport.calls[:7]] == [
        "eth_chainId",
        "eth_getBlockByNumber",
        "eth_getBlockByNumber",
        "eth_getBlockByNumber",
        "eth_getCode",
        "eth_getStorageAt",
        "eth_getBlockByNumber",
    ]
    assert transport.calls[-1]["url"].startswith(
        "https://sourcify.dev/server/v2/contract/1/"
    )
    assert transport.calls[-1]["headers"] == {
        "Accept": "application/json",
        "User-Agent": transport.calls[-1]["headers"]["User-Agent"],
    }

    stored = load_resolution_record(
        record["resolution_digest"],
        session_id="session-a",
        env=_env(tmp_path),
        validator=validate_resolution_record,
        digester=resolution_digest,
    )
    assert stored["rpc_url"] == "https://rpc.example/"
    assert stored["sourcify_target_url"] == transport.calls[-1]["url"]
    assert stored["resolution_digest"] == record["resolution_digest"]


def test_resolution_digest_excludes_display_only_sources(tmp_path) -> None:
    result = resolve_contract(
        _args(),
        SimpleNamespace(session_id="session-a", env=_env(tmp_path)),
        https_request=FakeHttps(),
    )
    record = load_resolution_record(
        result["data"]["resolution_digest"],
        session_id="session-a",
        env=_env(tmp_path),
        validator=validate_resolution_record,
        digester=resolution_digest,
    )
    changed = dict(record)
    changed["explorer_contract_url"] = "https://another.example/address"
    changed["research_source_urls"] = ["https://another.example/research"]
    changed["verification_statement"] = "Display text"

    assert resolution_digest(changed) == record["resolution_digest"]


def test_runtime_helpers_load_call_select_and_revalidate(tmp_path) -> None:
    context = SimpleNamespace(session_id="session-a", env=_env(tmp_path))
    result = resolve_contract(_args(), context, https_request=FakeHttps())
    record = load_resolution(result["data"]["resolution_digest"], context)

    assert function_by_signature(record, "balanceOf(address)").name == "balanceOf"
    assert rpc_call(record, "eth_chainId", [], https_request=FakeHttps()) == "0x1"
    assert revalidate_resolution(record, https_request=FakeHttps()) == {
        "block_number": "16",
        "block_hash": _LATEST_HASH,
    }


def test_resolve_contract_records_eip1967_implementation(tmp_path) -> None:
    result = resolve_contract(
        _args(),
        SimpleNamespace(session_id="session-a", env=_env(tmp_path)),
        https_request=FakeHttps(implementation=True),
    )

    assert result["ok"] is True
    assert result["data"]["proxy_kind"] == "eip1967"
    assert result["data"]["implementation_address"] == _IMPLEMENTATION
    assert result["data"]["abi_address"] == _IMPLEMENTATION


@pytest.mark.parametrize(
    ("transport", "expected_error"),
    [
        (FakeHttps(duplicate_abi=True), "ABI_INVALID"),
        (FakeHttps(stale_recheck=True), "STALE_BLOCK"),
        (FakeHttps(sourcify_status=404), "CONTRACT_NOT_VERIFIED"),
    ],
)
def test_resolve_contract_rejects_invalid_verification(
    tmp_path, transport: FakeHttps, expected_error: str
) -> None:
    result = resolve_contract(
        _args(),
        SimpleNamespace(session_id="session-a", env=_env(tmp_path)),
        https_request=transport,
    )

    assert result["ok"] is False
    assert result["state"] == "failed"
    assert result["error"]["code"] == expected_error
    assert result["error"]["retryable"] is False
