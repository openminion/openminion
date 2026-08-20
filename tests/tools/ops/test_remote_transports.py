from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from openminion.modules.runtime.credentials import resolve_credential_ref
from openminion.tools.ops.contracts import (
    KubernetesTarget,
    OperationRequest,
    OpsConfig,
    SsmTarget,
    WinrmTarget,
)
from openminion.tools.ops.profiles import build_argv
from openminion.tools.ops.transports import (
    KubernetesTransport,
    SsmTransport,
    WinrmTransport,
)


def _credential():
    return resolve_credential_ref(
        "ops-test",
        scope_kind="tool_family",
        scope_id="ops",
        env_name="OPENMINION_OPS_TEST",
    )


def _target(payload: dict[str, Any]):
    return OpsConfig.model_validate({"targets": [payload]}).targets[0]


def test_remote_target_variants_are_strict_and_scoped() -> None:
    credential = _credential()
    winrm = _target(
        {
            "target_id": "windows",
            "kind": "winrm",
            "address": "windows.example",
            "username": "operator",
            "credential_ref": credential,
            "ca_trust_path": "/etc/ssl/certs/ops.pem",
        }
    )
    kubernetes = _target(
        {
            "target_id": "pod",
            "kind": "kubernetes",
            "credential_ref": credential,
            "context": "staging",
            "namespace": "agents",
            "pod": "worker-0",
        }
    )
    ssm = _target(
        {
            "target_id": "node",
            "kind": "ssm",
            "credential_ref": credential,
            "account_id": "123456789012",
            "region": "us-west-2",
            "managed_node_id": "mi-123",
            "document_name": "AWS-RunShellScript",
        }
    )

    assert isinstance(winrm, WinrmTarget)
    assert isinstance(kubernetes, KubernetesTarget)
    assert isinstance(ssm, SsmTarget)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _target(
            {
                "target_id": "pod",
                "kind": "kubernetes",
                "credential_ref": credential,
                "context": "staging",
                "namespace": "agents",
                "pod": "worker-0",
                "password": "secret",
            }
        )
    with pytest.raises(ValidationError, match="HTTPS port 5986"):
        _target(
            {
                "target_id": "windows",
                "kind": "winrm",
                "address": "windows.example",
                "port": 5985,
                "username": "operator",
                "credential_ref": credential,
                "ca_trust_path": "/etc/ssl/certs/ops.pem",
            }
        )
    with pytest.raises(ValidationError, match="requires document"):
        _target(
            {
                "target_id": "node",
                "kind": "ssm",
                "credential_ref": credential,
                "account_id": "123456789012",
                "region": "us-west-2",
                "managed_node_id": "mi-123",
                "platform": "windows",
                "document_name": "AWS-RunShellScript",
            }
        )


def test_winrm_transport_preserves_argv_and_validates_tls() -> None:
    captured: dict[str, Any] = {}

    class Protocol:
        def open_shell(self, *, working_directory=None):
            captured["cwd"] = working_directory
            return "shell"

        def run_command(self, shell_id, command, args):
            captured["run"] = (shell_id, command, args)
            return "command"

        def get_command_output(self, shell_id, command_id):
            return b"ok", b"", 0

        def cleanup_command(self, shell_id, command_id):
            captured["cleanup"] = (shell_id, command_id)

        def close_shell(self, shell_id):
            captured["closed"] = shell_id

    def factory(**kwargs):
        captured["config"] = kwargs
        return Protocol()

    target = _target(
        {
            "target_id": "windows",
            "kind": "winrm",
            "address": "windows.example",
            "username": "operator",
            "credential_ref": _credential(),
            "ca_trust_path": "/etc/ssl/certs/ops.pem",
        }
    )
    result = WinrmTransport(
        lambda _ref: "credential",
        protocol_factory=factory,
    ).run(
        target,
        ("program.exe", "a&b", "$(literal)"),
        timeout_seconds=4,
        cwd="C:\\Ops",
    )

    assert result.return_code == 0
    assert captured["run"] == (
        "shell",
        "program.exe",
        ["a&b", "$(literal)"],
    )
    assert captured["config"]["endpoint"] == "https://windows.example:5986/wsman"
    assert captured["config"]["server_cert_validation"] == "validate"
    assert captured["config"]["ca_trust_path"] == "/etc/ssl/certs/ops.pem"


def test_winrm_transport_reports_timeout_without_output() -> None:
    class Protocol:
        def open_shell(self, *, working_directory=None):
            return "shell"

        def run_command(self, shell_id, command, args):
            return "command"

        def get_command_output(self, shell_id, command_id):
            raise TimeoutError

        def cleanup_command(self, shell_id, command_id):
            return None

        def close_shell(self, shell_id):
            return None

    target = _target(
        {
            "target_id": "windows",
            "kind": "winrm",
            "address": "windows.example",
            "username": "operator",
            "credential_ref": _credential(),
            "ca_trust_path": "/etc/ssl/certs/ops.pem",
        }
    )
    result = WinrmTransport(
        lambda _ref: "credential",
        protocol_factory=lambda **_kwargs: Protocol(),
    ).run(target, ("program.exe",), timeout_seconds=1)

    assert result.return_code == 124
    assert result.timed_out is True


def test_kubernetes_transport_uses_one_noninteractive_pod_exec() -> None:
    captured: dict[str, Any] = {}

    class Client:
        def connect_get_namespaced_pod_exec(self):
            raise AssertionError("stream owns this call")

    class Response:
        returncode = 7

        def run_forever(self, timeout):
            captured["timeout"] = timeout

        def is_open(self):
            return False

        def read_stdout(self):
            return "out"

        def read_stderr(self):
            return "err"

        def close(self):
            captured["closed"] = True

    def stream_call(*args, **kwargs):
        captured["stream"] = (args, kwargs)
        return Response()

    target = _target(
        {
            "target_id": "pod",
            "kind": "kubernetes",
            "credential_ref": _credential(),
            "context": "staging",
            "namespace": "agents",
            "pod": "worker-0",
            "kubernetes_container": "worker",
        }
    )
    result = KubernetesTransport(
        lambda _ref: "/tmp/kubeconfig",
        client_factory=lambda _target, _path: Client(),
        stream_call=stream_call,
    ).run(target, ("printf", "%s", "a;b"), timeout_seconds=3)

    assert result.return_code == 7
    args, kwargs = captured["stream"]
    assert args[1:] == ("worker-0", "agents")
    assert kwargs["command"] == ["printf", "%s", "a;b"]
    assert kwargs["stdin"] is False
    assert kwargs["tty"] is False
    assert kwargs["container"] == "worker"


def test_kubernetes_transport_reports_timeout_and_closes_stream() -> None:
    class Client:
        connect_get_namespaced_pod_exec = object()

    class Response:
        returncode = None
        closed = False

        def run_forever(self, timeout):
            return None

        def is_open(self):
            return not self.closed

        def read_stdout(self):
            return "partial"

        def read_stderr(self):
            return ""

        def close(self):
            self.closed = True

    response = Response()
    target = _target(
        {
            "target_id": "pod",
            "kind": "kubernetes",
            "credential_ref": _credential(),
            "context": "staging",
            "namespace": "agents",
            "pod": "worker-0",
        }
    )
    result = KubernetesTransport(
        lambda _ref: "/tmp/kubeconfig",
        client_factory=lambda _target, _path: Client(),
        stream_call=lambda *_args, **_kwargs: response,
    ).run(target, ("sleep", "10"), timeout_seconds=1)

    assert result.return_code == 124
    assert result.timed_out is True
    assert response.closed is True


def test_ssm_transport_sends_once_and_preserves_provider_id() -> None:
    class Client:
        def __init__(self):
            self.sent = 0

        def send_command(self, **kwargs):
            self.sent += 1
            self.request = kwargs
            return {"Command": {"CommandId": "cmd-123"}}

        def get_command_invocation(self, **kwargs):
            self.invocation = kwargs
            return {
                "Status": "Success",
                "ResponseCode": 0,
                "StandardOutputContent": "ok",
                "StandardErrorContent": "",
            }

        def cancel_command(self, **kwargs):
            self.cancelled = kwargs

    client = Client()
    target = _target(
        {
            "target_id": "node",
            "kind": "ssm",
            "credential_ref": _credential(),
            "account_id": "123456789012",
            "region": "us-west-2",
            "managed_node_id": "mi-123",
            "document_name": "AWS-RunShellScript",
        }
    )
    result = SsmTransport(
        lambda _ref: "staging-profile",
        client_factory=lambda _target, _profile: client,
        sleep=lambda _seconds: None,
    ).run(target, ("printf", "%s", "a;b"), timeout_seconds=3)

    assert client.sent == 1
    assert client.request["InstanceIds"] == ["mi-123"]
    assert client.request["DocumentName"] == "AWS-RunShellScript"
    assert client.request["Parameters"] == {"commands": ["printf %s 'a;b'"]}
    assert result.provider_request_id == "cmd-123"


def test_ssm_transport_times_out_without_redispatch() -> None:
    class Client:
        sent = 0
        cancelled = None

        def send_command(self, **kwargs):
            self.sent += 1
            return {"Command": {"CommandId": "cmd-timeout"}}

        def get_command_invocation(self, **kwargs):
            return {"Status": "InProgress"}

        def cancel_command(self, **kwargs):
            self.cancelled = kwargs

    times = iter((0.0, 0.0, 2.0))
    client = Client()
    target = _target(
        {
            "target_id": "node",
            "kind": "ssm",
            "credential_ref": _credential(),
            "account_id": "123456789012",
            "region": "us-west-2",
            "managed_node_id": "mi-123",
            "document_name": "AWS-RunShellScript",
        }
    )
    result = SsmTransport(
        lambda _ref: "staging-profile",
        client_factory=lambda _target, _profile: client,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(times),
    ).run(target, ("true",), timeout_seconds=1)

    assert client.sent == 1
    assert client.cancelled == {
        "CommandId": "cmd-timeout",
        "InstanceIds": ["mi-123"],
    }
    assert result.timed_out is True
    assert result.provider_request_id == "cmd-timeout"


def test_ssm_transport_preserves_id_when_poll_outcome_is_unknown() -> None:
    class Client:
        def send_command(self, **kwargs):
            return {"Command": {"CommandId": "cmd-unknown"}}

        def get_command_invocation(self, **kwargs):
            raise ConnectionError

    target = _target(
        {
            "target_id": "node",
            "kind": "ssm",
            "credential_ref": _credential(),
            "account_id": "123456789012",
            "region": "us-west-2",
            "managed_node_id": "mi-123",
            "document_name": "AWS-RunShellScript",
        }
    )

    with pytest.raises(RuntimeError, match="cmd-unknown"):
        SsmTransport(
            lambda _ref: "staging-profile",
            client_factory=lambda _target, _profile: Client(),
        ).run(target, ("true",), timeout_seconds=1)


@pytest.mark.parametrize(
    ("profile_id", "parameters", "expected_script", "expected_tail"),
    [
        ("host.snapshot", {}, "Get-CimInstance Win32_OperatingSystem", ()),
        ("service.inspect", {"service": "a;whoami"}, "Get-Service", ("a;whoami",)),
        ("logs.query", {"service": "svc", "limit": 5}, "Get-WinEvent", ("svc", "5")),
        ("network.inspect", {}, "Get-NetTCPConnection", ()),
        ("disk.usage", {}, "Win32_LogicalDisk", ()),
        ("memory.usage", {}, "FreePhysicalMemory", ()),
        ("process.list", {}, "Get-Process", ()),
        ("process.inspect", {"pid": 42}, "Get-Process -Id", ("42",)),
        (
            "network.port_owner",
            {"port": 443, "protocol": "tcp"},
            "Get-NetTCPConnection",
            ("443",),
        ),
    ],
)
def test_windows_profiles_use_fixed_scripts_and_data_arguments(
    profile_id, parameters, expected_script, expected_tail
) -> None:
    argv = build_argv(
        OperationRequest(
            operation_id="profile",
            target_id="windows",
            profile_id=profile_id,
            parameters=parameters,
        ),
        target_platform="windows",
    )

    assert argv[:4] == (
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    )
    assert expected_script in argv[4]
    assert argv[5:] == expected_tail
