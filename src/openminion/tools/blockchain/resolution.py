from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import Field, TypeAdapter, field_validator
from web3 import Web3

from openminion.base.version import OPENMINION_VERSION
from openminion.modules.tool.runtime.public_https import (
    PublicHttpsError,
    PublicHttpsResponse,
    request_public_https,
)

from .abi import abi_signature
from .preparations import (
    MAX_RESOLUTION_RECORD_BYTES,
    SessionRecordError,
    load_resolution_record,
    save_resolution_record,
)
from .schema_types import (
    Address,
    ClosedModel,
    DecimalString,
    FunctionAbi,
    TransactionHash,
)

_EIP1967_IMPLEMENTATION_SLOT = (
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
)
_GENESIS_PARENT_HASH = "0x" + "00" * 32
_DECIMAL_STRING_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_MAX_BODY_BYTES = 2 * 1024 * 1024
_TIMEOUT_SECONDS = 15.0
_USER_AGENT = (
    f"OpenMinion/{OPENMINION_VERSION} (+https://github.com/openminion/openminion)"
)
_HttpsRequest = Callable[..., PublicHttpsResponse]


def _validate_https_url(value: str, *, query_allowed: bool) -> str:
    normalized = value.strip()
    if len(normalized) > 2048:
        raise ValueError("URL must be at most 2048 characters")
    parsed = urlsplit(normalized)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("URL must be a valid HTTPS URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (not query_allowed and parsed.query)
    ):
        raise ValueError("URL must be a valid HTTPS URL")
    return normalized


def _normalized_rpc_url(value: str) -> str:
    parsed = urlsplit(value)
    host = str(parsed.hostname).lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if parsed.port in {None, 443} else f"{host}:{parsed.port}"
    return urlunsplit(("https", netloc, parsed.path or "/", "", ""))


def _url_origin(value: str) -> str:
    parsed = urlsplit(value)
    host = str(parsed.hostname).lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if parsed.port in {None, 443} else f"{host}:{parsed.port}"
    return f"https://{netloc}"


class ExpectedCheckpoint(ClosedModel):
    block_number: DecimalString
    block_hash: TransactionHash


class ResolveContractArgs(ClosedModel):
    rpc_url: str
    expected_chain_id: int = Field(ge=1)
    expected_genesis_hash: TransactionHash
    expected_checkpoint: ExpectedCheckpoint | None = None
    contract_address: Address
    explorer_contract_url: str | None = None
    research_source_urls: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("rpc_url")
    @classmethod
    def validate_rpc_url(cls, value: str) -> str:
        return _validate_https_url(value, query_allowed=False)

    @field_validator("explorer_contract_url")
    @classmethod
    def validate_explorer_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_https_url(value, query_allowed=True)

    @field_validator("research_source_urls")
    @classmethod
    def validate_source_urls(cls, values: list[str]) -> list[str]:
        return [
            _validate_https_url(value, query_allowed=True) for value in values
        ]


class ResolvedContractRecord(ClosedModel):
    resolution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    rpc_url: str
    sourcify_target_url: str
    sourcify_implementation_url: str | None
    expected_chain_id: int = Field(ge=1)
    observed_chain_id: int = Field(ge=1)
    expected_genesis_hash: TransactionHash
    observed_genesis_hash: TransactionHash
    expected_checkpoint: ExpectedCheckpoint | None
    observed_checkpoint: ExpectedCheckpoint | None
    verification_block_number: DecimalString
    verification_block_hash: TransactionHash
    contract_address: Address
    proxy_kind: Literal["direct", "eip1967"]
    implementation_address: Address | None
    abi_address: Address
    target_code_hash: TransactionHash
    implementation_code_hash: TransactionHash | None
    function_abi: list[FunctionAbi]
    explorer_contract_url: str | None
    research_source_urls: list[str]
    verification_statement: str


class ResolvedContractData(ClosedModel):
    resolution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    rpc_origin: str
    sourcify_origin: Literal["https://sourcify.dev"]
    expected_chain_id: int = Field(ge=1)
    observed_chain_id: int = Field(ge=1)
    expected_genesis_hash: TransactionHash
    observed_genesis_hash: TransactionHash
    expected_checkpoint: ExpectedCheckpoint | None
    observed_checkpoint: ExpectedCheckpoint | None
    verification_block_number: DecimalString
    verification_block_hash: TransactionHash
    contract_address: Address
    proxy_kind: Literal["direct", "eip1967"]
    implementation_address: Address | None
    abi_address: Address
    target_code_hash: TransactionHash
    implementation_code_hash: TransactionHash | None
    function_abi: list[FunctionAbi]
    explorer_contract_url: str | None
    research_source_urls: list[str]
    verification_statement: str


class ResolveContractSuccess(ClosedModel):
    ok: Literal[True]
    state: Literal["succeeded"]
    action: Literal["resolve_contract"]
    data: ResolvedContractData


ResolutionErrorCode = Literal[
    "INVALID_ARGUMENT",
    "ENDPOINT_FORBIDDEN",
    "RPC_UNAVAILABLE",
    "CHAIN_IDENTITY_MISMATCH",
    "STALE_BLOCK",
    "STALE_RESOLUTION",
    "EMPTY_CONTRACT_CODE",
    "UNSUPPORTED_PROXY",
    "METADATA_UNAVAILABLE",
    "CONTRACT_NOT_VERIFIED",
    "ABI_INVALID",
    "RESULT_TOO_LARGE",
]


class ResolutionError(ClosedModel):
    code: ResolutionErrorCode
    message: str
    retryable: Literal[False]
    details: dict[str, Any]


class ResolveContractError(ClosedModel):
    ok: Literal[False]
    state: Literal["failed"]
    error: ResolutionError


ResolveContractResult = Annotated[
    ResolveContractSuccess | ResolveContractError,
    Field(discriminator="ok"),
]
RESOLVE_CONTRACT_RESULT_ADAPTER: TypeAdapter[ResolveContractResult] = TypeAdapter(
    ResolveContractResult
)


class ResolutionFailure(RuntimeError):
    def __init__(
        self,
        code: ResolutionErrorCode,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


def resolution_error_result(failure: ResolutionFailure) -> dict[str, Any]:
    result = {
        "ok": False,
        "state": "failed",
        "error": {
            "code": failure.code,
            "message": failure.message,
            "retryable": False,
            "details": failure.details,
        },
    }
    return RESOLVE_CONTRACT_RESULT_ADAPTER.validate_python(result).model_dump(
        mode="json"
    )


def _request(
    https_request: _HttpsRequest,
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: Mapping[str, str],
) -> PublicHttpsResponse:
    return https_request(
        url,
        method=method,
        body=body,
        headers=headers,
        timeout=_TIMEOUT_SECONDS,
        max_body_bytes=_MAX_BODY_BYTES,
    )


class _RpcClient:
    def __init__(self, url: str, https_request: _HttpsRequest) -> None:
        self.url = url
        self.https_request = https_request
        self.request_id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        self.request_id += 1
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": self.request_id,
                "method": method,
                "params": params,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            response = _request(
                self.https_request,
                self.url,
                method="POST",
                body=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": _USER_AGENT,
                },
            )
        except PublicHttpsError as exc:
            code: ResolutionErrorCode = (
                "ENDPOINT_FORBIDDEN"
                if exc.code
                in {
                    "FORBIDDEN_DESTINATION",
                    "MIXED_DESTINATIONS",
                    "PEER_MISMATCH",
                    "INVALID_URL",
                }
                else "RPC_UNAVAILABLE"
            )
            raise ResolutionFailure(
                code,
                "Blockchain RPC request failed.",
                {"operation": method, "reason": exc.code},
            ) from exc
        if response.status != 200:
            raise ResolutionFailure(
                "RPC_UNAVAILABLE",
                "Blockchain RPC request failed.",
                {"operation": method, "http_status": response.status},
            )
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResolutionFailure(
                "RPC_UNAVAILABLE",
                "Blockchain RPC returned invalid JSON.",
                {"operation": method},
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("jsonrpc") != "2.0"
            or payload.get("id") != self.request_id
            or "result" not in payload
            or payload.get("error") is not None
        ):
            raise ResolutionFailure(
                "RPC_UNAVAILABLE",
                "Blockchain RPC returned an invalid response.",
                {"operation": method},
            )
        return payload["result"]


def _quantity(value: Any, *, operation: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned an invalid quantity.",
            {"operation": operation},
        )
    try:
        return int(value, 16)
    except ValueError as exc:
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned an invalid quantity.",
            {"operation": operation},
        ) from exc


def _block(value: Any, *, operation: str) -> tuple[int, str]:
    if not isinstance(value, dict):
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned an invalid block.",
            {"operation": operation},
        )
    number = _quantity(value.get("number"), operation=operation)
    block_hash = _transaction_hash(value.get("hash"), operation=operation)
    return number, block_hash


def _transaction_hash(value: Any, *, operation: str) -> str:
    if not isinstance(value, str):
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned an invalid transaction hash.",
            {"operation": operation},
        )
    normalized = value.lower()
    if (
        len(normalized) != 66
        or not normalized.startswith("0x")
        or any(
            character not in "0123456789abcdef" for character in normalized[2:]
        )
    ):
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned an invalid transaction hash.",
            {"operation": operation},
        )
    return normalized


def _hex_data(value: Any, *, operation: str, exact_bytes: int | None = None) -> str:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned invalid hexadecimal data.",
            {"operation": operation},
        )
    body = value[2:]
    if len(body) % 2 or any(character not in "0123456789abcdefABCDEF" for character in body):
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned invalid hexadecimal data.",
            {"operation": operation},
        )
    if exact_bytes is not None and len(body) != exact_bytes * 2:
        raise ResolutionFailure(
            "RPC_UNAVAILABLE",
            "Blockchain RPC returned invalid hexadecimal data.",
            {"operation": operation},
        )
    return f"0x{body.lower()}"


def _code_hash(code: str) -> str:
    return f"0x{Web3.keccak(hexstr=code).hex().removeprefix('0x')}"


def _sourcify_url(chain_id: int, address: str) -> str:
    return (
        "https://sourcify.dev/server/v2/contract/"
        f"{chain_id}/{quote(address, safe='')}"
        "?fields=abi,runtimeBytecode.onchainBytecode"
    )


def _project_parameter(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("ABI parameter must be an object")
    projected = {"name": value.get("name", ""), "type": value.get("type")}
    if "components" in value:
        components = value["components"]
        if not isinstance(components, list):
            raise ValueError("ABI components must be a list")
        projected["components"] = [
            _project_parameter(component) for component in components
        ]
    return projected


def _canonical_function_abi(value: Any) -> list[FunctionAbi]:
    if not isinstance(value, list):
        raise ResolutionFailure("ABI_INVALID", "Verified ABI is invalid.")
    functions: list[FunctionAbi] = []
    try:
        for entry in value:
            if not isinstance(entry, dict) or entry.get("type") != "function":
                continue
            inputs = entry.get("inputs")
            outputs = entry.get("outputs")
            if not isinstance(inputs, list) or not isinstance(outputs, list):
                raise ValueError("function inputs and outputs must be lists")
            functions.append(
                FunctionAbi.model_validate(
                    {
                        "type": "function",
                        "name": entry.get("name"),
                        "inputs": [_project_parameter(item) for item in inputs],
                        "outputs": [_project_parameter(item) for item in outputs],
                        "stateMutability": entry.get("stateMutability"),
                    }
                )
            )
    except (TypeError, ValueError) as exc:
        raise ResolutionFailure("ABI_INVALID", "Verified ABI is invalid.") from exc
    ordered = sorted(functions, key=abi_signature)
    signatures = [abi_signature(item) for item in ordered]
    if len(signatures) != len(set(signatures)):
        raise ResolutionFailure(
            "ABI_INVALID",
            "Verified ABI contains duplicate function signatures.",
        )
    return ordered


def _sourcify_contract(
    chain_id: int,
    address: str,
    rpc_code: str,
    https_request: _HttpsRequest,
) -> tuple[str, list[FunctionAbi]]:
    url = _sourcify_url(chain_id, address)
    try:
        response = _request(
            https_request,
            url,
            headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
        )
    except PublicHttpsError as exc:
        raise ResolutionFailure(
            "METADATA_UNAVAILABLE",
            "Verified contract metadata is unavailable.",
            {"reason": exc.code},
        ) from exc
    if response.status == 404:
        raise ResolutionFailure(
            "CONTRACT_NOT_VERIFIED",
            "Contract is not verified by Sourcify.",
        )
    if response.status != 200:
        raise ResolutionFailure(
            "METADATA_UNAVAILABLE",
            "Verified contract metadata is unavailable.",
            {"http_status": response.status},
        )
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResolutionFailure(
            "METADATA_UNAVAILABLE",
            "Verified contract metadata is invalid.",
        ) from exc
    if not isinstance(payload, dict):
        raise ResolutionFailure(
            "METADATA_UNAVAILABLE",
            "Verified contract metadata is invalid.",
        )
    runtime = payload.get("runtimeBytecode")
    metadata_chain_id = payload.get("chainId")
    metadata_address = payload.get("address")
    if (
        not isinstance(metadata_chain_id, str)
        or _DECIMAL_STRING_RE.fullmatch(metadata_chain_id) is None
        or not isinstance(metadata_address, str)
        or not Web3.is_checksum_address(metadata_address)
    ):
        raise ResolutionFailure(
            "METADATA_UNAVAILABLE",
            "Verified contract metadata is invalid.",
        )
    try:
        metadata_code = _hex_data(
            runtime["onchainBytecode"],
            operation="sourcify_runtime_bytecode",
        )
    except (KeyError, TypeError, ValueError, ResolutionFailure) as exc:
        raise ResolutionFailure(
            "METADATA_UNAVAILABLE",
            "Verified contract metadata is invalid.",
        ) from exc
    if (
        payload.get("match") not in {"match", "exact_match"}
        or metadata_chain_id != str(chain_id)
        or metadata_address != address
        or _code_hash(metadata_code) != _code_hash(rpc_code)
    ):
        raise ResolutionFailure(
            "CONTRACT_NOT_VERIFIED",
            "Sourcify metadata does not match the deployed contract.",
        )
    return url, _canonical_function_abi(payload.get("abi"))


_DIGEST_EXCLUDED_FIELDS = {
    "resolution_digest",
    "explorer_contract_url",
    "research_source_urls",
    "verification_statement",
}


def resolution_digest(record: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in record.items()
        if key not in _DIGEST_EXCLUDED_FIELDS
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def validate_resolution_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return ResolvedContractRecord.model_validate(record).model_dump(mode="json")


def load_resolution(resolution_digest_value: str, context: Any) -> dict[str, Any]:
    return load_resolution_record(
        resolution_digest_value,
        session_id=str(getattr(context, "session_id", "") or ""),
        env=getattr(context, "env", None),
        validator=validate_resolution_record,
        digester=resolution_digest,
    )


def rpc_call(
    record: Mapping[str, Any],
    method: str,
    params: list[Any],
    *,
    https_request: _HttpsRequest = request_public_https,
) -> Any:
    normalized = validate_resolution_record(record)
    return _RpcClient(normalized["rpc_url"], https_request).call(method, params)


def function_by_signature(
    record: Mapping[str, Any], signature: str
) -> FunctionAbi:
    normalized = validate_resolution_record(record)
    matches: list[FunctionAbi] = []
    for raw_function in normalized["function_abi"]:
        function = FunctionAbi.model_validate(raw_function)
        if abi_signature(function) == signature:
            matches.append(function)
    if len(matches) != 1:
        raise ValueError("function signature is not present exactly once")
    return matches[0]


def _observe_chain_and_lineage(
    rpc: _RpcClient,
    *,
    expected_chain_id: int,
    expected_genesis_hash: str,
    expected_checkpoint: Mapping[str, Any] | None,
    contract_address: str,
    revalidation: bool,
) -> dict[str, Any]:
    observed_chain_id = _quantity(rpc.call("eth_chainId", []), operation="eth_chainId")
    genesis_value = rpc.call("eth_getBlockByNumber", ["0x0", False])
    genesis_number, genesis_hash = _block(
        genesis_value,
        operation="eth_getBlockByNumber:genesis",
    )
    genesis_parent_hash = _transaction_hash(
        genesis_value.get("parentHash") if isinstance(genesis_value, Mapping) else None,
        operation="eth_getBlockByNumber:genesis",
    )
    if (
        observed_chain_id != expected_chain_id
        or genesis_number != 0
        or genesis_hash != expected_genesis_hash.lower()
        or genesis_parent_hash != _GENESIS_PARENT_HASH
    ):
        raise ResolutionFailure(
            "CHAIN_IDENTITY_MISMATCH",
            "Observed chain identity does not match the expected chain.",
            {
                "expected_chain_id": expected_chain_id,
                "observed_chain_id": observed_chain_id,
                "expected_genesis_hash": expected_genesis_hash.lower(),
                "observed_genesis_hash": genesis_hash,
            },
        )

    observed_checkpoint: dict[str, str] | None = None
    if expected_checkpoint is not None:
        checkpoint_number = int(expected_checkpoint["block_number"])
        observed_number, observed_hash = _block(
            rpc.call("eth_getBlockByNumber", [hex(checkpoint_number), False]),
            operation="eth_getBlockByNumber:checkpoint",
        )
        observed_checkpoint = {
            "block_number": str(observed_number),
            "block_hash": observed_hash,
        }
        if (
            observed_number != checkpoint_number
            or observed_hash != str(expected_checkpoint["block_hash"]).lower()
        ):
            raise ResolutionFailure(
                "CHAIN_IDENTITY_MISMATCH",
                "Observed checkpoint does not match the expected chain.",
                {
                    "checkpoint_block_number": str(checkpoint_number),
                    "expected_checkpoint_hash": str(
                        expected_checkpoint["block_hash"]
                    ).lower(),
                    "observed_checkpoint_hash": observed_hash,
                },
            )

    block_number, block_hash = _block(
        rpc.call("eth_getBlockByNumber", ["latest", False]),
        operation="eth_getBlockByNumber:latest",
    )
    block_tag = hex(block_number)
    pinned_number, pinned_hash = _block(
        rpc.call("eth_getBlockByNumber", [block_tag, False]),
        operation="eth_getBlockByNumber:pinned",
    )
    if pinned_number != block_number or pinned_hash != block_hash:
        raise ResolutionFailure(
            "STALE_BLOCK",
            "Pinned block changed before contract inspection.",
        )

    target_code = _hex_data(
        rpc.call("eth_getCode", [contract_address, block_tag]),
        operation="eth_getCode:target",
    )
    if target_code == "0x":
        raise ResolutionFailure(
            "STALE_RESOLUTION" if revalidation else "EMPTY_CONTRACT_CODE",
            (
                "Resolved contract lineage changed."
                if revalidation
                else "Target address has no contract code at the verification block."
            ),
        )
    storage = _hex_data(
        rpc.call(
            "eth_getStorageAt",
            [contract_address, _EIP1967_IMPLEMENTATION_SLOT, block_tag],
        ),
        operation="eth_getStorageAt:eip1967",
        exact_bytes=32,
    )
    implementation_address: str | None = None
    implementation_code: str | None = None
    if int(storage, 16) != 0:
        if int(storage[2:26], 16) != 0:
            raise ResolutionFailure(
                "STALE_RESOLUTION" if revalidation else "UNSUPPORTED_PROXY",
                (
                    "Resolved proxy lineage changed."
                    if revalidation
                    else "EIP-1967 implementation storage is not a canonical address."
                ),
            )
        implementation_address = Web3.to_checksum_address(f"0x{storage[-40:]}")
        implementation_code = _hex_data(
            rpc.call("eth_getCode", [implementation_address, block_tag]),
            operation="eth_getCode:implementation",
        )
        if implementation_code == "0x":
            raise ResolutionFailure(
                "STALE_RESOLUTION" if revalidation else "UNSUPPORTED_PROXY",
                (
                    "Resolved implementation code changed."
                    if revalidation
                    else "EIP-1967 implementation address has no contract code."
                ),
            )

    repeated_number, repeated_hash = _block(
        rpc.call("eth_getBlockByNumber", [block_tag, False]),
        operation="eth_getBlockByNumber:recheck",
    )
    if repeated_number != block_number or repeated_hash != block_hash:
        raise ResolutionFailure(
            "STALE_BLOCK",
            "Pinned block changed during contract inspection.",
        )
    return {
        "observed_chain_id": observed_chain_id,
        "observed_genesis_hash": genesis_hash,
        "observed_checkpoint": observed_checkpoint,
        "block_number": block_number,
        "block_hash": block_hash,
        "target_code": target_code,
        "implementation_address": implementation_address,
        "implementation_code": implementation_code,
    }


def revalidate_resolution(
    record: Mapping[str, Any],
    *,
    https_request: _HttpsRequest = request_public_https,
) -> dict[str, str]:
    normalized = validate_resolution_record(record)
    facts = _observe_chain_and_lineage(
        _RpcClient(normalized["rpc_url"], https_request),
        expected_chain_id=normalized["expected_chain_id"],
        expected_genesis_hash=normalized["expected_genesis_hash"],
        expected_checkpoint=normalized["expected_checkpoint"],
        contract_address=normalized["contract_address"],
        revalidation=True,
    )
    if (
        _code_hash(facts["target_code"]) != normalized["target_code_hash"]
        or facts["implementation_address"] != normalized["implementation_address"]
        or (
            _code_hash(facts["implementation_code"])
            if facts["implementation_code"] is not None
            else None
        )
        != normalized["implementation_code_hash"]
    ):
        raise ResolutionFailure(
            "STALE_RESOLUTION",
            "Resolved contract lineage changed.",
        )
    return {
        "block_number": str(facts["block_number"]),
        "block_hash": facts["block_hash"],
    }


def _resolve(
    request: ResolveContractArgs,
    *,
    https_request: _HttpsRequest,
) -> dict[str, Any]:
    rpc_url = _normalized_rpc_url(request.rpc_url)
    target_address = Web3.to_checksum_address(request.contract_address)
    expected_checkpoint = (
        request.expected_checkpoint.model_dump(mode="json")
        if request.expected_checkpoint is not None
        else None
    )
    facts = _observe_chain_and_lineage(
        _RpcClient(rpc_url, https_request),
        expected_chain_id=request.expected_chain_id,
        expected_genesis_hash=request.expected_genesis_hash,
        expected_checkpoint=expected_checkpoint,
        contract_address=target_address,
        revalidation=False,
    )
    observed_chain_id = facts["observed_chain_id"]
    implementation_address = facts["implementation_address"]
    implementation_code = facts["implementation_code"]

    target_url, target_abi = _sourcify_contract(
        observed_chain_id,
        target_address,
        facts["target_code"],
        https_request,
    )
    implementation_url: str | None = None
    function_abi = target_abi
    abi_address = target_address
    if implementation_address is not None and implementation_code is not None:
        implementation_url, function_abi = _sourcify_contract(
            observed_chain_id,
            implementation_address,
            implementation_code,
            https_request,
        )
        abi_address = implementation_address

    record: dict[str, Any] = {
        "resolution_digest": "sha256:" + "0" * 64,
        "rpc_url": rpc_url,
        "sourcify_target_url": target_url,
        "sourcify_implementation_url": implementation_url,
        "expected_chain_id": request.expected_chain_id,
        "observed_chain_id": observed_chain_id,
        "expected_genesis_hash": request.expected_genesis_hash.lower(),
        "observed_genesis_hash": facts["observed_genesis_hash"],
        "expected_checkpoint": expected_checkpoint,
        "observed_checkpoint": facts["observed_checkpoint"],
        "verification_block_number": str(facts["block_number"]),
        "verification_block_hash": facts["block_hash"],
        "contract_address": target_address,
        "proxy_kind": "eip1967" if implementation_address else "direct",
        "implementation_address": implementation_address,
        "abi_address": abi_address,
        "target_code_hash": _code_hash(facts["target_code"]),
        "implementation_code_hash": (
            _code_hash(implementation_code) if implementation_code else None
        ),
        "function_abi": [item.model_dump(mode="json") for item in function_abi],
        "explorer_contract_url": request.explorer_contract_url,
        "research_source_urls": request.research_source_urls,
        "verification_statement": (
            "Chain identity, pinned-block code, and Sourcify metadata were internally "
            "consistent for this contract candidate. This does not establish that "
            "the deployment or network is official."
        ),
    }
    record["resolution_digest"] = resolution_digest(record)
    return validate_resolution_record(record)


def _public_resolution_data(record: Mapping[str, Any]) -> dict[str, Any]:
    hidden = {"rpc_url", "sourcify_target_url", "sourcify_implementation_url"}
    return {
        key: value for key, value in record.items() if key not in hidden
    } | {
        "rpc_origin": _url_origin(str(record["rpc_url"])),
        "sourcify_origin": "https://sourcify.dev",
    }


def resolve_contract(
    args: dict[str, Any],
    context: Any,
    *,
    https_request: _HttpsRequest = request_public_https,
) -> dict[str, Any]:
    try:
        request = ResolveContractArgs.model_validate(args)
        record = _resolve(request, https_request=https_request)
        result = {
            "ok": True,
            "state": "succeeded",
            "action": "resolve_contract",
            "data": _public_resolution_data(record),
        }
        encoded = json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        if len(encoded) > MAX_RESOLUTION_RECORD_BYTES:
            raise ResolutionFailure(
                "RESULT_TOO_LARGE",
                "Resolved contract result exceeds the size limit.",
            )
        save_resolution_record(
            record,
            context,
            validator=validate_resolution_record,
            digester=resolution_digest,
        )
        return RESOLVE_CONTRACT_RESULT_ADAPTER.validate_python(result).model_dump(
            mode="json"
        )
    except ResolutionFailure as failure:
        return resolution_error_result(failure)
    except SessionRecordError as exc:
        reason = (
            "result_too_large"
            if exc.reason == "size_limit"
            else "session_persistence_unavailable"
        )
        code: ResolutionErrorCode = (
            "RESULT_TOO_LARGE" if reason == "result_too_large" else "INVALID_ARGUMENT"
        )
        return resolution_error_result(
            ResolutionFailure(
                code,
                "Resolved contract facts could not be stored for this session.",
                {"reason": reason},
            )
        )
    except (TypeError, ValueError) as exc:
        return resolution_error_result(
            ResolutionFailure(
                "INVALID_ARGUMENT",
                "Contract resolution arguments are invalid.",
                {"reason": exc.__class__.__name__},
            )
        )


__all__ = [
    "ExpectedCheckpoint",
    "RESOLVE_CONTRACT_RESULT_ADAPTER",
    "ResolveContractArgs",
    "ResolveContractError",
    "ResolveContractResult",
    "ResolveContractSuccess",
    "ResolvedContractRecord",
    "ResolvedContractData",
    "ResolutionError",
    "ResolutionFailure",
    "function_by_signature",
    "load_resolution",
    "revalidate_resolution",
    "resolution_digest",
    "resolution_error_result",
    "resolve_contract",
    "rpc_call",
    "validate_resolution_record",
]
