from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shlex
import time
from typing import Any, Literal

from openminion.base.generated_paths import resolve_generated_root

MatrixClassification = Literal[
    "pass",
    "provider_residual",
    "runtime_regression",
    "blocked_external",
    "not_applicable",
]

SCHEMA_VERSION = "session-context-provider-matrix.v1"
CERTIFICATION_RUN_SCHEMA_VERSION = "provider-session-resilience-run.v1"
CERTIFICATION_REPORT_SCHEMA_VERSION = "provider-session-resilience-certification.v1"
CERTIFICATION_REPORT_DIRNAME = "provider-session-resilience-certification"
_ALLOWED_INJECTED_FAILURE_CODES = frozenset(
    {
        "auth_denied",
        "model_access_denied",
        "quota_or_rate_limit",
        "timeout",
        "malformed_output",
        "structured_output_failed",
        "transient_transport",
    }
)
_SCRATCH_ROOT = (
    Path(__file__).resolve().parents[6]
    / "workspace-tmp"
    / "session-context-memory-operational-utility"
)
_TARGETS = (
    (
        "minimax-direct",
        "test-configs/per-agent-minimax-official.json",
        "minimax-m2-7",
    ),
    (
        "openrouter-gpt4o-mini",
        "test-configs/per-agent-openrouter-gpt-4o-mini.json",
        "hello-agent",
    ),
    (
        "openrouter-haiku45",
        "test-configs/per-agent-openrouter-claude-haiku-4-5.json",
        "hello-agent",
    ),
)


@dataclass(frozen=True)
class ProviderMatrixRow:
    provider_class: str
    model: str
    profile: str
    agent_id: str
    session_id: str
    command: str
    classification: MatrixClassification
    failure_code: str
    transcript_ref: str
    trace_ref: str
    redaction_status: str


@dataclass(frozen=True)
class ProviderMatrix:
    schema_version: str
    run_id: str
    generated_at_epoch: int
    rows: tuple[ProviderMatrixRow, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "generated_at_epoch": self.generated_at_epoch,
            "rows": [asdict(row) for row in self.rows],
        }


@dataclass(frozen=True)
class ProviderSessionTarget:
    provider_class: str
    adapter: str
    api_protocol: str
    endpoint_authority: str
    config_ref: str
    agent_id: str
    expected_model: str
    required_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ProviderSessionInjectedFailure:
    provider_class: str
    failure_code: str
    retry_eligible: bool


@dataclass(frozen=True)
class ProviderSessionResilienceManifest:
    run_id: str
    messages: tuple[str, ...]
    required_output_marker: str
    targets: tuple[ProviderSessionTarget, ...]
    injected_failures: tuple[ProviderSessionInjectedFailure, ...]


def build_provider_matrix(
    *, root: Path | None = None, run_id: str | None = None
) -> ProviderMatrix:
    repo_root = root or Path(__file__).resolve().parents[6]
    resolved_run_id = run_id or f"scmu-provider-matrix-{int(time.time())}"
    rows = tuple(
        _row_for_target(repo_root=repo_root, run_id=resolved_run_id, target=target)
        for target in _TARGETS
    )
    return ProviderMatrix(
        schema_version=SCHEMA_VERSION,
        run_id=resolved_run_id,
        generated_at_epoch=int(time.time()),
        rows=rows,
    )


def provider_class_key(
    *, adapter: str, api_protocol: str, endpoint_authority: str
) -> str:
    return "|".join(
        (
            adapter.strip().lower(),
            api_protocol.strip().lower(),
            endpoint_authority.strip().lower(),
        )
    )


def load_provider_session_resilience_manifest(
    path: Path,
    *,
    root: Path | None = None,
) -> ProviderSessionResilienceManifest:
    repo_root = root or Path(__file__).resolve().parents[6]
    payload = _read_json(path)
    if payload.get("schema_version") != CERTIFICATION_RUN_SCHEMA_VERSION:
        raise ValueError("unsupported provider session resilience schema_version")
    if _has_secret_bearing_field(payload):
        raise ValueError("manifest contains a secret-bearing field")

    run_id = _required_str(payload, "run_id")
    messages_payload = payload.get("messages")
    if not isinstance(messages_payload, list) or len(messages_payload) < 2:
        raise ValueError("manifest requires at least two messages")
    messages = tuple(str(message).strip() for message in messages_payload)
    if not all(messages):
        raise ValueError("manifest messages cannot be empty")
    required_output_marker = _required_str(payload, "required_output_marker")
    targets_payload = payload.get("targets")
    if not isinstance(targets_payload, list) or len(targets_payload) < 2:
        raise ValueError("manifest requires at least two provider targets")

    targets = tuple(
        _parse_provider_session_target(target, repo_root=repo_root)
        for target in targets_payload
    )
    class_keys = {
        provider_class_key(
            adapter=target.adapter,
            api_protocol=target.api_protocol,
            endpoint_authority=target.endpoint_authority,
        )
        for target in targets
    }
    if len(class_keys) < 2:
        raise ValueError("manifest must include at least two distinct provider classes")
    if len(class_keys) != len(targets):
        raise ValueError("duplicate provider class targets are not allowed")

    failures = tuple(
        _parse_injected_failure(item) for item in payload.get("injected_failures", ())
    )
    return ProviderSessionResilienceManifest(
        run_id=run_id,
        messages=messages,
        required_output_marker=required_output_marker,
        targets=targets,
        injected_failures=failures,
    )


def write_provider_session_resilience_report(
    manifest: ProviderSessionResilienceManifest,
    *,
    manifest_path: Path,
    root: Path | None = None,
    validation_only: bool = True,
    rows: list[dict[str, object]] | None = None,
) -> tuple[Path, Path]:
    repo_root = root or Path(__file__).resolve().parents[6]
    output_root = (
        resolve_generated_root(repo_root)
        / CERTIFICATION_REPORT_DIRNAME
        / _safe_segment(manifest.run_id)
    )
    output_root.mkdir(parents=True, exist_ok=True)
    report_rows = (
        rows
        if rows is not None
        else [
            build_provider_session_certification_row(
                target=target,
                run_id=manifest.run_id,
                messages=manifest.messages,
                classification="blocked_external"
                if validation_only
                else "not_applicable",
                failure_code="validate_only_live_not_run" if validation_only else "",
            )
            for target in manifest.targets
        ]
    )
    payload = {
        "schema_version": CERTIFICATION_REPORT_SCHEMA_VERSION,
        "run_id": manifest.run_id,
        "generated_at_epoch": int(time.time()),
        "validation_only": validation_only,
        "manifest_path": str(manifest_path.resolve(strict=False)),
        "rows": report_rows,
        "injected_failures": [asdict(item) for item in manifest.injected_failures],
        "redaction_status": "redacted",
    }
    json_path = output_root / "certification-report.json"
    markdown_path = output_root / "certification-report.md"
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_render_certification_markdown(payload), encoding="utf-8")
    return json_path, markdown_path


def write_provider_matrix(
    matrix: ProviderMatrix, *, root: Path | None = None
) -> tuple[Path, Path]:
    repo_root = root or Path(__file__).resolve().parents[6]
    output_root = (
        resolve_generated_root(repo_root)
        / "session-context-memory-operational-utility"
        / matrix.run_id
    )
    output_root.mkdir(parents=True, exist_ok=True)
    json_path = output_root / "provider-matrix.json"
    markdown_path = output_root / "provider-matrix.md"
    json_path.write_text(
        json.dumps(matrix.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_render_markdown(matrix), encoding="utf-8")
    return json_path, markdown_path


def load_provider_matrix(path: Path) -> ProviderMatrix:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported provider matrix schema_version")
    rows = tuple(ProviderMatrixRow(**row) for row in payload.get("rows", ()))
    return ProviderMatrix(
        schema_version=str(payload["schema_version"]),
        run_id=str(payload["run_id"]),
        generated_at_epoch=int(payload["generated_at_epoch"]),
        rows=rows,
    )


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest must be a JSON object")
    return payload


def _parse_provider_session_target(
    payload: object,
    *,
    repo_root: Path,
) -> ProviderSessionTarget:
    if not isinstance(payload, dict):
        raise ValueError("provider target must be a JSON object")
    provider_class = _require_mapping_keys(
        payload,
        "provider_class",
        ("adapter", "api_protocol", "endpoint_authority"),
    )
    capabilities = payload.get("required_capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("provider target requires capability facts")
    normalized_capabilities = tuple(str(item).strip() for item in capabilities)
    if not all(normalized_capabilities):
        raise ValueError("provider target capability facts cannot be empty")

    config_ref = _required_str(payload, "config_ref")
    config_path = Path(config_ref).expanduser()
    if not config_path.is_absolute():
        config_path = repo_root / config_path
    if not config_path.exists():
        raise ValueError(f"provider config does not exist: {config_ref}")

    return ProviderSessionTarget(
        provider_class=provider_class_key(
            adapter=str(provider_class["adapter"]),
            api_protocol=str(provider_class["api_protocol"]),
            endpoint_authority=str(provider_class["endpoint_authority"]),
        ),
        adapter=str(provider_class["adapter"]),
        api_protocol=str(provider_class["api_protocol"]),
        endpoint_authority=str(provider_class["endpoint_authority"]),
        config_ref=config_ref,
        agent_id=_required_str(payload, "agent_id"),
        expected_model=_required_str(payload, "expected_model"),
        required_capabilities=normalized_capabilities,
    )


def _parse_injected_failure(payload: object) -> ProviderSessionInjectedFailure:
    if not isinstance(payload, dict):
        raise ValueError("injected failure must be a JSON object")
    failure_code = _required_str(payload, "failure_code")
    if failure_code not in _ALLOWED_INJECTED_FAILURE_CODES:
        raise ValueError(f"unsupported injected failure_code: {failure_code}")
    return ProviderSessionInjectedFailure(
        provider_class=_required_str(payload, "provider_class"),
        failure_code=failure_code,
        retry_eligible=bool(payload.get("retry_eligible")),
    )


def build_provider_session_certification_row(
    *,
    target: ProviderSessionTarget,
    run_id: str,
    messages: tuple[str, ...],
    classification: MatrixClassification,
    failure_code: str,
    latency_ms: int | None = None,
) -> dict[str, object]:
    return {
        "provider_class": target.provider_class,
        "adapter": target.adapter,
        "api_protocol": target.api_protocol,
        "endpoint_authority": target.endpoint_authority,
        "model": target.expected_model,
        "agent_id": target.agent_id,
        "session_id": f"{run_id}:{target.provider_class}",
        "command": shlex.join(
            provider_session_probe_args(
                target,
                run_id=run_id,
                messages=tuple(
                    f"<redacted-message-{index + 1}>" for index in range(len(messages))
                ),
                required_output_marker="<redacted-continuity-marker>",
            )
        ),
        "required_capabilities": list(target.required_capabilities),
        "classification": classification,
        "failure_code": failure_code,
        "retry_fallback_owner": "",
        "final_provider_class": target.provider_class
        if classification == "pass"
        else "",
        "latency_ms": latency_ms,
        "token_count": None,
        "cost": None,
        "quality_result": (
            "passed_two_turn_probe" if classification == "pass" else "not_evaluated"
        ),
        "transcript_ref": "",
        "trace_ref": "",
        "redaction_status": "redacted",
    }


def provider_session_probe_args(
    target: ProviderSessionTarget,
    *,
    run_id: str,
    messages: tuple[str, ...],
    required_output_marker: str,
) -> tuple[str, ...]:
    args = [
        "tests/e2e/runners/run_cli_chat_probe.py",
        "--config",
        target.config_ref,
        "--agent",
        target.agent_id,
        "--session",
        f"{run_id}:{target.provider_class}",
    ]
    for message in messages:
        args.extend(("--message", message))
    args.extend(("--require-output-marker", required_output_marker))
    return tuple(args)


def _render_certification_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Provider Session Resilience Certification ({payload['run_id']})",
        "",
        "| Provider class | Agent | Model | Classification | Failure code |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in payload["rows"]:
        lines.append(
            "| "
            f"{row['provider_class']} | {row['agent_id']} | {row['model']} | "
            f"{row['classification']} | {row['failure_code'] or '-'} |"
        )
    if payload["injected_failures"]:
        lines.extend(("", "## Injected Failures", ""))
        for failure in payload["injected_failures"]:
            lines.append(
                "- "
                f"{failure['provider_class']}: {failure['failure_code']} "
                f"(retry_eligible={failure['retry_eligible']})"
            )
    return "\n".join(lines) + "\n"


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing required field: {key}")
    return value.strip()


def _require_mapping_keys(
    payload: dict[str, Any],
    key: str,
    required_keys: tuple[str, ...],
) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"missing required mapping: {key}")
    missing = [item for item in required_keys if item not in value]
    if missing:
        raise ValueError(f"{key} is missing required keys: {', '.join(missing)}")
    return value


def _has_secret_bearing_field(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(
                token in lowered
                for token in ("secret", "api_key", "token", "password", "credential")
            ):
                return True
            if _has_secret_bearing_field(child):
                return True
    if isinstance(value, list):
        return any(_has_secret_bearing_field(item) for item in value)
    return False


def _safe_segment(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)


def _row_for_target(
    *, repo_root: Path, run_id: str, target: tuple[str, str, str]
) -> ProviderMatrixRow:
    provider_class, config_rel, agent_id = target
    config_path = repo_root / config_rel
    command = "session-context-memory operational probe"
    session_id = f"{run_id}:{provider_class}"
    transcript_ref = _write_scratch_transcript(
        run_id=run_id,
        provider_class=provider_class,
        body=f"config={config_rel}\nagent={agent_id}\ncommand={command}\n",
    )
    if not config_path.exists():
        return _blocked_row(
            provider_class=provider_class,
            agent_id=agent_id,
            session_id=session_id,
            command=command,
            transcript_ref=transcript_ref,
            failure_code="config_missing",
        )
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    agent = dict((payload.get("agents") or {}).get(agent_id) or {})
    provider = str(agent.get("provider") or provider_class)
    model = str(agent.get("model") or agent.get("model_id") or "")
    missing_env = _missing_env_placeholders(payload)
    if missing_env:
        return ProviderMatrixRow(
            provider_class=provider_class,
            model=model,
            profile=provider,
            agent_id=agent_id,
            session_id=session_id,
            command=command,
            classification="blocked_external",
            failure_code=f"missing_env:{','.join(missing_env)}",
            transcript_ref=transcript_ref,
            trace_ref="",
            redaction_status="redacted",
        )
    return ProviderMatrixRow(
        provider_class=provider_class,
        model=model,
        profile=provider,
        agent_id=agent_id,
        session_id=session_id,
        command=command,
        classification="not_applicable",
        failure_code="live_execution_not_requested_by_harness_test",
        transcript_ref=transcript_ref,
        trace_ref="",
        redaction_status="redacted",
    )


def _blocked_row(
    *,
    provider_class: str,
    agent_id: str,
    session_id: str,
    command: str,
    transcript_ref: str,
    failure_code: str,
) -> ProviderMatrixRow:
    return ProviderMatrixRow(
        provider_class=provider_class,
        model="",
        profile="",
        agent_id=agent_id,
        session_id=session_id,
        command=command,
        classification="blocked_external",
        failure_code=failure_code,
        transcript_ref=transcript_ref,
        trace_ref="",
        redaction_status="redacted",
    )


def _missing_env_placeholders(payload: object) -> tuple[str, ...]:
    text = json.dumps(payload, sort_keys=True)
    names = set()
    for chunk in text.split("${")[1:]:
        name = chunk.split("}", 1)[0].strip()
        if name and not os.getenv(name):
            names.add(name)
    return tuple(sorted(names))


def _write_scratch_transcript(*, run_id: str, provider_class: str, body: str) -> str:
    root = _SCRATCH_ROOT / run_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{provider_class}.redacted.txt"
    path.write_text(body, encoding="utf-8")
    return str(path)


def _render_markdown(matrix: ProviderMatrix) -> str:
    lines = [
        f"# Session Context Provider Matrix ({matrix.run_id})",
        "",
        "| Provider class | Agent | Model | Classification | Failure code |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in matrix.rows:
        lines.append(
            "| "
            f"{row.provider_class} | {row.agent_id} | {row.model or '-'} | "
            f"{row.classification} | {row.failure_code or '-'} |"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "CERTIFICATION_REPORT_SCHEMA_VERSION",
    "CERTIFICATION_RUN_SCHEMA_VERSION",
    "ProviderMatrix",
    "ProviderMatrixRow",
    "ProviderSessionInjectedFailure",
    "ProviderSessionResilienceManifest",
    "ProviderSessionTarget",
    "build_provider_matrix",
    "build_provider_session_certification_row",
    "load_provider_matrix",
    "load_provider_session_resilience_manifest",
    "provider_class_key",
    "provider_session_probe_args",
    "write_provider_session_resilience_report",
    "write_provider_matrix",
]
