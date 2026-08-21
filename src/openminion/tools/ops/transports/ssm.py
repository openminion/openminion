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
_TERMINAL = frozenset({"Success", "Cancelled", "TimedOut", "Failed", "Cancelling"})


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
        response = client.send_command(
            InstanceIds=[ssm_target.managed_node_id],
            DocumentName=ssm_target.document_name,
            Parameters={"commands": [_command_for(ssm_target, argv)]},
            TimeoutSeconds=max(1, int(timeout_seconds)),
        )
        command_id = str(response["Command"]["CommandId"])
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
                except (OSError, RuntimeError, ValueError) as exc:
                    raise RuntimeError(
                        "SSM command outcome is unknown after provider acceptance "
                        f"({command_id}): {type(exc).__name__}"
                    ) from exc
                status = str(invocation["Status"])
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
        finally:
            if operation_id:
                with self._lock:
                    self._active.pop(operation_id, None)
        stdout, stdout_cut = bounded(str(invocation.get("StandardOutputContent", "")))
        stderr, stderr_cut = bounded(str(invocation.get("StandardErrorContent", "")))
        if output_sink is not None:
            if stdout:
                output_sink("stdout", stdout)
            if stderr:
                output_sink("stderr", stderr)
        return TransportResult(
            argv=argv,
            return_code=int(
                invocation.get("ResponseCode", 0 if status == "Success" else 1)
            ),
            stdout=stdout,
            stderr=stderr,
            cancelled=status in {"Cancelled", "Cancelling"},
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
