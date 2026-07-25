from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import time
from typing import Literal

from openminion.base.generated_paths import resolve_generated_root

MatrixClassification = Literal[
    "pass",
    "provider_residual",
    "runtime_regression",
    "blocked_external",
    "not_applicable",
]

SCHEMA_VERSION = "session-context-provider-matrix.v1"
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
    "ProviderMatrix",
    "ProviderMatrixRow",
    "build_provider_matrix",
    "load_provider_matrix",
    "write_provider_matrix",
]
