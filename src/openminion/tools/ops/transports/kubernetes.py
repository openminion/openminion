from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast

from ..contracts import (
    KubernetesTarget,
    OperationTarget,
    TransportFacts,
    TransportResult,
)
from ..interfaces import OutputSink
from .runtime import bounded
from .ssh import CredentialReader


class ExecResponse(Protocol):
    returncode: int | None

    def run_forever(self, timeout: float) -> None: ...

    def is_open(self) -> bool: ...

    def read_stdout(self) -> str: ...

    def read_stderr(self) -> str: ...

    def close(self) -> None: ...


ClientFactory = Callable[[OperationTarget, str], Any]
StreamCall = Callable[..., ExecResponse]
ReadClientFactory = Callable[[KubernetesTarget, str], tuple[Any, Any]]


class KubernetesTransport:
    def __init__(
        self,
        credential_reader: CredentialReader,
        *,
        client_factory: ClientFactory | None = None,
        stream_call: StreamCall | None = None,
        read_client_factory: ReadClientFactory | None = None,
    ) -> None:
        self._credential_reader = credential_reader
        self._client_factory = client_factory
        self._stream_call = stream_call
        self._read_client_factory = read_client_factory
        self._active: dict[str, ExecResponse] = {}
        self._lock = threading.RLock()

    def connect(self, target: OperationTarget) -> TransportFacts:
        self._validate_target(target)
        return TransportFacts(
            kind="kubernetes",
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
        k8s_target = cast(KubernetesTarget, target)
        if cwd:
            raise ValueError("kubernetes exec does not support command cwd")
        assert k8s_target.credential_ref is not None
        client = self._new_client(
            k8s_target, self._credential_reader(k8s_target.credential_ref)
        )
        response = self._stream(
            client.connect_get_namespaced_pod_exec,
            k8s_target.pod,
            k8s_target.namespace,
            command=list(argv),
            container=k8s_target.kubernetes_container or None,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        if operation_id:
            with self._lock:
                self._active[operation_id] = response
        try:
            response.run_forever(timeout=timeout_seconds)
            timed_out = response.is_open()
            if timed_out:
                response.close()
            stdout, stdout_cut = bounded(response.read_stdout())
            stderr, stderr_cut = bounded(response.read_stderr())
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(f"Kubernetes exec failed: {type(exc).__name__}") from exc
        finally:
            if operation_id:
                with self._lock:
                    self._active.pop(operation_id, None)
            response.close()
        if output_sink is not None:
            if stdout:
                output_sink("stdout", stdout)
            if stderr:
                output_sink("stderr", stderr)
        return TransportResult(
            argv=argv,
            return_code=124 if timed_out else int(response.returncode or 0),
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            truncated=stdout_cut or stderr_cut,
        )

    def cancel(self, operation_id: str) -> bool:
        with self._lock:
            response = self._active.get(operation_id)
        if response is None:
            return False
        response.close()
        return True

    def inspect_resource(
        self,
        target: KubernetesTarget,
        operation: str,
        parameters: Mapping[str, object],
    ) -> dict[str, Any]:
        if parameters.get("context") != target.context:
            raise ValueError("kubernetes request context does not match target")
        if parameters.get("namespace") != target.namespace:
            raise ValueError("kubernetes request namespace does not match target")
        assert target.credential_ref is not None
        core, apps = self._new_read_clients(
            target, self._credential_reader(target.credential_ref)
        )
        if operation == "logs_get":
            value = core.read_namespaced_pod_log(
                name=str(parameters["pod"]),
                namespace=target.namespace,
                container=str(parameters.get("container") or "") or None,
                tail_lines=int(parameters["tail_lines"]),
            )
            return {"logs": str(value)}
        if operation == "events_list":
            involved = str(parameters.get("involved_object") or "")
            value = core.list_namespaced_event(
                namespace=target.namespace,
                field_selector=(f"involvedObject.name={involved}" if involved else ""),
                limit=int(parameters["limit"]),
            )
            return {"items": [_model_payload(item) for item in value.items]}
        kind = str(parameters["kind"]).lower()
        readers = _workload_readers(core, apps, kind)
        if operation == "workload_get":
            value = readers[0](name=str(parameters["name"]), namespace=target.namespace)
            return {"item": _model_payload(value)}
        if operation == "workload_list":
            value = readers[1](namespace=target.namespace)
            return {"items": [_model_payload(item) for item in value.items]}
        if operation == "rollout_status":
            if kind == "pod":
                raise ValueError("pod does not have a rollout status")
            value = readers[0](name=str(parameters["name"]), namespace=target.namespace)
            return {"status": _model_payload(value.status)}
        raise ValueError(f"unknown Kubernetes operation: {operation}")

    def close(self) -> None:
        with self._lock:
            operation_ids = tuple(self._active)
        for operation_id in operation_ids:
            self.cancel(operation_id)

    def _new_client(self, target: KubernetesTarget, kubeconfig_path: str) -> Any:
        if self._client_factory is not None:
            return self._client_factory(target, kubeconfig_path)
        try:
            from kubernetes import client, config
        except ImportError as exc:
            raise RuntimeError(
                "Kubernetes operations require the optional "
                "'remote-kubernetes' dependency"
            ) from exc
        api_client = config.new_client_from_config(
            config_file=kubeconfig_path,
            context=target.context,
        )
        return client.CoreV1Api(api_client)

    def _new_read_clients(
        self, target: KubernetesTarget, kubeconfig_path: str
    ) -> tuple[Any, Any]:
        if self._read_client_factory is not None:
            return self._read_client_factory(target, kubeconfig_path)
        try:
            from kubernetes import client, config
        except ImportError as exc:
            raise RuntimeError(
                "Kubernetes operations require the optional "
                "'remote-kubernetes' dependency"
            ) from exc
        api_client = config.new_client_from_config(
            config_file=kubeconfig_path,
            context=target.context,
        )
        return client.CoreV1Api(api_client), client.AppsV1Api(api_client)

    def _stream(self, *args: object, **kwargs: object) -> ExecResponse:
        if self._stream_call is not None:
            return self._stream_call(*args, **kwargs)
        from kubernetes.stream import stream

        return cast(ExecResponse, stream(*args, **kwargs))

    @staticmethod
    def _validate_target(target: OperationTarget) -> None:
        if target.kind != "kubernetes":
            raise ValueError("kubernetes transport requires a kubernetes target")


__all__ = ["KubernetesTransport"]


def _workload_readers(core: Any, apps: Any, kind: str) -> tuple[Any, Any]:
    if kind == "pod":
        return core.read_namespaced_pod, core.list_namespaced_pod
    readers = {
        "deployment": (
            apps.read_namespaced_deployment_status,
            apps.list_namespaced_deployment,
        ),
        "statefulset": (
            apps.read_namespaced_stateful_set_status,
            apps.list_namespaced_stateful_set,
        ),
        "daemonset": (
            apps.read_namespaced_daemon_set_status,
            apps.list_namespaced_daemon_set,
        ),
    }
    try:
        return readers[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported Kubernetes workload kind: {kind}") from exc


def _model_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    return value.to_dict()
