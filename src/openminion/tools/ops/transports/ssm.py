"""AWS Systems Manager command and inventory transport."""

from __future__ import annotations

import base64
import json
import shlex
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, cast

from ..contracts import OperationTarget, SsmTarget, TransportFacts, TransportResult
from ..interfaces import OutputSink
from .runtime import bounded
from .ssh import CredentialReader

ClientFactory = Callable[[OperationTarget, str], Any]
_PENDING = frozenset({"Pending", "InProgress", "Delayed"})
_CANCELLED = frozenset({"Cancelled", "Cancelling"})
_TERMINAL = frozenset({"Success", "Cancelled", "TimedOut", "Failed", "Cancelling"})


def _read_ssm_command_id(response: object) -> str:
    if not isinstance(response, Mapping):
        raise RuntimeError(
            f"SSM send_command response invalid: {type(response).__name__}"
        )
    command = response.get("Command")
    if not isinstance(command, Mapping):
        raise RuntimeError("SSM send_command response invalid: Command")
    command_id = command.get("CommandId")
    if not isinstance(command_id, str) or not command_id.strip():
        detail = (
            "missing"
            if command_id is None or command_id == ""
            else type(command_id).__name__
            if not isinstance(command_id, str)
            else "blank"
        )
        raise RuntimeError(f"SSM send_command response invalid: {detail}")
    return command_id.strip()


def _read_ssm_status(invocation: object, command_id: str) -> str:
    if not isinstance(invocation, Mapping):
        raise RuntimeError(
            f"SSM command terminal status invalid ({command_id}): "
            f"{type(invocation).__name__}"
        )
    raw_status = invocation.get("Status")
    if not isinstance(raw_status, str) or not raw_status.strip():
        detail = (
            "missing"
            if raw_status is None
            else "blank"
            if isinstance(raw_status, str)
            else type(raw_status).__name__
        )
        raise RuntimeError(
            f"SSM command terminal status invalid ({command_id}): {detail}"
        )
    raw_details = invocation.get("StatusDetails")
    if raw_details is not None and not isinstance(raw_details, str):
        raise RuntimeError(
            f"SSM command terminal status invalid ({command_id}): "
            f"StatusDetails {type(raw_details).__name__}"
        )
    status = raw_status.strip()
    if status not in _PENDING and status not in _TERMINAL:
        raise RuntimeError(
            f"SSM command terminal status invalid ({command_id}): {status}"
        )
    return status


def _read_ssm_return_code(
    invocation: Mapping[str, object], *, status: str, command_id: str
) -> int:
    raw_code = invocation.get("ResponseCode")
    if isinstance(raw_code, bool) or not isinstance(raw_code, int):
        detail = "missing" if raw_code is None else type(raw_code).__name__
        raise RuntimeError(
            f"SSM command terminal status invalid ({command_id}): {detail}"
        )
    if status == "Success":
        if raw_code != 0:
            raise RuntimeError(
                f"SSM command outcome is unknown after provider acceptance "
                f"({command_id}): Success with ResponseCode={raw_code}"
            )
        return 0
    if status == "Failed":
        if raw_code in (0, -1):
            raw_details = invocation.get("StatusDetails")
            details_suffix = (
                f" ({raw_details.strip()})"
                if isinstance(raw_details, str) and raw_details.strip()
                else ""
            )
            raise RuntimeError(
                f"SSM command outcome is unknown after provider acceptance "
                f"({command_id}): Failed{details_suffix} with ResponseCode={raw_code}"
            )
        return raw_code
    return raw_code


def _command_for(target: SsmTarget, argv: tuple[str, ...]) -> str:
    if target.platform != "windows":
        return shlex.join(argv)
    payload = base64.b64encode(json.dumps(argv).encode()).decode()
    return (
        "$a=ConvertFrom-Json([Text.Encoding]::UTF8.GetString("
        f"[Convert]::FromBase64String('{payload}')));& $a[0] @($a[1..($a.Count-1)])"
    )


class SsmTransport:
    def __init__(
        self,
        credential_reader: CredentialReader,
        *,
        client_factory: ClientFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._credential_reader = credential_reader
        self._client_factory = client_factory
        self._sleep = sleep
        self._monotonic = monotonic
        self._active: dict[str, tuple[Any, str, str]] = {}
        self._lock = threading.RLock()

    def connect(self, target: OperationTarget) -> TransportFacts:
        self._validate_target(target)
        return TransportFacts(
            kind="ssm",
            platform=target.platform,
            connected=True,
            capabilities=target.capabilities,
        )

    def inspect(self, target: OperationTarget) -> TransportFacts:
        return self.connect(target)

    def run(
        self,
        target: OperationTarget,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float,
        operation_id: str = "",
        output_sink: OutputSink | None = None,
        cwd: str = "",
    ) -> TransportResult:
        self._validate_target(target)
        ssm_target = cast(SsmTarget, target)
        if cwd:
            raise ValueError("ssm run command does not support command cwd")
        assert ssm_target.credential_ref is not None
        client = self._new_client(
            ssm_target, self._credential_reader(ssm_target.credential_ref)
        )
        try:
            response = client.send_command(
                InstanceIds=[ssm_target.managed_node_id],
                DocumentName=ssm_target.document_name,
                Parameters={"commands": [_command_for(ssm_target, argv)]},
                TimeoutSeconds=max(1, int(timeout_seconds)),
            )
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"SSM send_command failed: {type(exc).__name__}"
            ) from exc
        command_id = _read_ssm_command_id(response)
        if operation_id:
            with self._lock:
                self._active[operation_id] = (
                    client,
                    command_id,
                    ssm_target.managed_node_id,
                )
        deadline = self._monotonic() + timeout_seconds
        try:
            while True:
                try:
                    invocation = client.get_command_invocation(
                        CommandId=command_id,
                        InstanceId=ssm_target.managed_node_id,
                    )
                except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "SSM command outcome is unknown after provider acceptance "
                        f"({command_id}): {type(exc).__name__}"
                    ) from exc
                status = _read_ssm_status(invocation, command_id)
                if status in _TERMINAL:
                    break
                if self._monotonic() >= deadline:
                    client.cancel_command(
                        CommandId=command_id,
                        InstanceIds=[ssm_target.managed_node_id],
                    )
                    return TransportResult(
                        argv=argv,
                        return_code=124,
                        timed_out=True,
                        provider_request_id=command_id,
                    )
                self._sleep(0.2)
            return_code = _read_ssm_return_code(
                invocation, status=status, command_id=command_id
            )
        finally:
            if operation_id:
                with self._lock:
                    self._active.pop(operation_id, None)
        raw_stdout = invocation.get("StandardOutputContent")
        raw_stderr = invocation.get("StandardErrorContent")
        stdout, stdout_cut = bounded("" if raw_stdout is None else str(raw_stdout))
        stderr, stderr_cut = bounded("" if raw_stderr is None else str(raw_stderr))
        if output_sink is not None:
            if stdout:
                output_sink("stdout", stdout)
            if stderr:
                output_sink("stderr", stderr)
        return TransportResult(
            argv=argv,
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
            cancelled=status in _CANCELLED,
            timed_out=status == "TimedOut",
            truncated=stdout_cut or stderr_cut,
            provider_request_id=command_id,
        )

    def cancel(self, operation_id: str) -> bool:
        with self._lock:
            active = self._active.get(operation_id)
        if active is None:
            return False
        client, command_id, managed_node_id = active
        client.cancel_command(
            CommandId=command_id,
            InstanceIds=[managed_node_id],
        )
        return True

    def inspect_resource(
        self,
        target: SsmTarget,
        operation: str,
        parameters: Mapping[str, object],
    ) -> dict[str, Any]:
        if parameters.get("account_id") != target.account_id:
            raise ValueError("SSM request account does not match target")
        if parameters.get("region") != target.region:
            raise ValueError("SSM request region does not match target")
        assert target.credential_ref is not None
        client = self._new_client(
            target, self._credential_reader(target.credential_ref)
        )
        if operation == "ssm_inventory":
            filters = []
            tag_key = str(parameters.get("tag_key") or "")
            tag_value = str(parameters.get("tag_value") or "")
            if bool(tag_key) != bool(tag_value):
                raise ValueError("SSM inventory tag key and value must be paired")
            if tag_key:
                filters.append({"Key": f"tag:{tag_key}", "Values": [tag_value]})
            return client.describe_instance_information(
                Filters=filters,
                MaxResults=min(int(parameters["limit"]), 50),
            )
        if operation == "ssm_command_status":
            return client.get_command_invocation(
                CommandId=str(parameters["command_id"]),
                InstanceId=target.managed_node_id,
            )
        raise ValueError(f"unknown SSM operation: {operation}")

    def close(self) -> None:
        with self._lock:
            operation_ids = tuple(self._active)
        for operation_id in operation_ids:
            self.cancel(operation_id)

    def _new_client(self, target: SsmTarget, profile_name: str) -> Any:
        if self._client_factory is not None:
            return self._client_factory(target, profile_name)
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "AWS SSM operations require the optional 'remote-aws' dependency"
            ) from exc
        return boto3.Session(
            profile_name=profile_name,
            region_name=target.region,
        ).client("ssm")

    @staticmethod
    def _validate_target(target: OperationTarget) -> None:
        if target.kind != "ssm":
            raise ValueError("ssm transport requires an ssm target")


__all__ = ["SsmTransport"]
