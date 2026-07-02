from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from .meta.schemas import MetaMetrics, MetaResult
from .schemas import Command, PolicyDecision, WorkingState

BRAIN_ADAPTER_INTERFACE_VERSION = "v1"
BRAIN_RUNNER_INTERFACE_VERSION = "v1"


class BrainRuntimeError(RuntimeError):
    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


StateMachineError = BrainRuntimeError


@runtime_checkable
class RLMAPI(Protocol):
    contract_version: str

    def generate(
        self,
        *,
        session_id: str,
        agent_id: str,
        purpose: str,
        query: str,
        ts: dict[str, Any] | None = None,
        budgets: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        meta_directive: dict[str, Any] | None = None,
        agent_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class SessionAPI(Protocol):
    contract_version: str

    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        attachments: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str: ...

    def append_event(
        self,
        session_id: str,
        type: str,
        payload: dict[str, Any],
        *,
        actor_type: str = "system",
        actor_id: str | None = None,
        trace: dict[str, Any] | None = None,
        refs: dict[str, Any] | None = None,
        parent_event_id: str | None = None,
        importance: int = 1,
        redaction: str | None = None,
        agent_id: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        parent_id: str | None = None,
        artifact_refs: list[str] | None = None,
        memory_refs: list[str] | None = None,
        status: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> str: ...

    def emit_canonical_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        actor_type: str = "system",
        actor_id: str | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        importance: int = 1,
    ) -> str: ...

    def put_working_state(
        self,
        session_id: str,
        *,
        state_ref: str | None = None,
        state_inline: dict[str, Any] | None = None,
    ) -> int: ...

    def get_latest_working_state(self, session_id: str) -> dict[str, Any] | None: ...

    def update_session_status(self, session_id: str, status: str) -> None: ...

    def list_turns(self, session_id: str) -> list[dict[str, Any]]: ...

    def list_events(self, session_id: str) -> list[dict[str, Any]]: ...

    def get_slice(
        self,
        session_id: str,
        purpose: str,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class ContextAPI(Protocol):
    contract_version: str

    def build(
        self,
        *,
        session_id: str,
        agent_id: str,
        purpose: str,
        budget: dict[str, Any],
        hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def make_delta(
        self,
        *,
        session_id: str,
        agent_id: str,
        content: str = "",
    ) -> str | dict[str, Any]: ...

    def maybe_compact(self, *, session_id: str, agent_id: str) -> bool: ...


@runtime_checkable
class LLMAPI(Protocol):
    contract_version: str

    def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int: ...

    def call_structured(
        self,
        *,
        model: str,
        purpose: str,
        context: dict[str, Any],
        schema: type[BaseModel],
    ) -> dict[str, Any]: ...


@runtime_checkable
class ToolAPI(Protocol):
    contract_version: str

    def execute(
        self, *, command: dict[str, Any], session_id: str, trace_id: str
    ) -> dict[str, Any]: ...


@runtime_checkable
class A2AAPI(Protocol):
    contract_version: str

    def call(
        self, *, command: dict[str, Any], session_id: str, trace_id: str
    ) -> dict[str, Any]: ...

    def poll_task(
        self, *, task_id: str, session_id: str, trace_id: str
    ) -> dict[str, Any]: ...

    def cancel_task(
        self, *, task_id: str, session_id: str, trace_id: str
    ) -> dict[str, Any]: ...


@runtime_checkable
class MemoryAPI(Protocol):
    contract_version: str

    def put_record(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> str: ...

    def stage_candidate(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        confidence: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str: ...

    def apply_outcome_feedback(
        self,
        *,
        record_ids: list[str],
        outcome: Literal["success", "failed", "timeout"],
        command_id: str,
        observed_at: str,
        feedback_delta: float,
    ) -> int: ...


@runtime_checkable
class SkillAPI(Protocol):
    contract_version: str

    def catalog_summaries(
        self,
        agent_id: str,
        status_filter: list[str] | str | None = None,
    ) -> list[dict[str, Any]]: ...

    def match(
        self,
        intent_text: str,
        step_hint: dict[str, Any] | None,
        agent_id: str,
        k: int = 3,
        status_filter: list[str] | str | None = None,
    ) -> list[Any]: ...

    def get_recipe(
        self,
        skill_id: str,
        version_hash: str | None = None,
    ) -> Any | None: ...

    def get_workflow(
        self,
        workflow_id: str,
        *,
        agent_id: str | None = None,
        status_filter: list[str] | str | None = None,
        scope: str | None = None,
    ) -> Any: ...

    def log_run(
        self,
        session_id: str,
        agent_id: str,
        skill_id: str,
        version_hash: str,
        used_for: str,
        outcome: str,
        evidence_refs: list[str] | None = None,
    ) -> str: ...

    def render_snippet(
        self,
        skill_id: str,
        version_hash: str | None = None,
        purpose: str = "plan",
        max_tokens: int = 500,
    ) -> tuple[str, str]: ...


@runtime_checkable
class PolicyAPI(Protocol):
    contract_version: str

    def evaluate(
        self,
        *,
        command: Command,
        working_state: WorkingState,
        session_context: dict[str, Any],
    ) -> PolicyDecision: ...


@runtime_checkable
class MetaAPI(Protocol):
    contract_version: str

    def evaluate(self, metrics: MetaMetrics) -> MetaResult: ...


@runtime_checkable
class SafetyAPI(Protocol):
    """Protocol for safety service integration."""

    contract_version: str

    def is_normal(self) -> bool: ...

    def stop(self, *, session_id: str | None = None, reason: str = "") -> bool: ...

    def kill(self, *, session_id: str | None = None, reason: str = "") -> bool: ...

    def panic(self, *, session_id: str | None = None, reason: str = "") -> bool: ...


@runtime_checkable
class RetrieveAPI(Protocol):
    contract_version: str

    def retrieve(
        self, query: str, *, top_k: int = 10, **kwargs: Any
    ) -> list[dict[str, Any]]: ...

    def retrieve_with_context(
        self,
        query: str,
        context: dict[str, Any],
        *,
        top_k: int = 10,
        **kwargs: Any,
    ) -> list[dict[str, Any]]: ...

    def ingest_skill(
        self,
        *,
        skill_id: str,
        version_hash: str,
        source_ref: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@runtime_checkable
class RunnerAPI(Protocol):
    contract_version: str

    def run(
        self,
        *,
        session_id: str,
        user_input: str | None = None,
        trace_id: str | None = None,
    ) -> Any: ...

    def step(
        self,
        *,
        session_id: str,
        user_input: str | None = None,
        trace_id: str | None = None,
    ) -> Any: ...


_REQUIRED_MEMBERS: dict[str, tuple[str, ...]] = {
    "session": (
        "contract_version",
        "append_turn",
        "append_event",
        "put_working_state",
        "get_latest_working_state",
        "list_turns",
    ),
    "context": (
        "contract_version",
        "build",
        "make_delta",
        "maybe_compact",
    ),
    "llm": (
        "contract_version",
        "estimate_tokens",
        "call_structured",
    ),
    "tool": (
        "contract_version",
        "execute",
    ),
    "a2a": (
        "contract_version",
        "call",
    ),
    "memory": (
        "contract_version",
        "put_record",
        "stage_candidate",
    ),
    "policy": (
        "contract_version",
        "evaluate",
    ),
    "safety": (
        "contract_version",
        "is_normal",
        "stop",
        "kill",
        "panic",
    ),
    "meta": (
        "contract_version",
        "evaluate",
    ),
    "rlm": (
        "contract_version",
        "generate",
    ),
    "retrieve": (
        "contract_version",
        "retrieve",
        "retrieve_with_context",
        "ingest_skill",
    ),
}

_RUNNER_REQUIRED_MEMBERS: tuple[str, ...] = (
    "contract_version",
    "run",
    "step",
)


def _missing_required_members(
    target: Any,
    required_members: tuple[str, ...],
) -> list[str]:
    missing: list[str] = []
    for name in required_members:
        if not hasattr(target, name):
            missing.append(name)
            continue
        value = getattr(target, name)
        if name != "contract_version" and not callable(value):
            missing.append(name)
    return missing


def _ensure_contract_compatibility(
    target: Any,
    *,
    required_members: tuple[str, ...],
    expected_version: str,
    contract_label: str,
) -> None:
    missing = _missing_required_members(target, required_members)
    if missing:
        raise TypeError(
            f"{target.__class__.__name__} is incompatible with {contract_label} contract; missing members: {', '.join(missing)}"
        )

    version = str(getattr(target, "contract_version", "")).strip()
    if version != expected_version:
        raise TypeError(
            f"{target.__class__.__name__} has unsupported contract_version={version!r}; expected {expected_version!r}"
        )


def ensure_adapter_compatibility(adapter: Any, *, adapter_type: str) -> None:
    """Fail fast when an adapter drifts from the common brain contract."""

    normalized = str(adapter_type or "").strip().lower()
    required = _REQUIRED_MEMBERS.get(normalized)
    if required is None:
        raise ValueError(f"unknown adapter_type: {adapter_type}")
    _ensure_contract_compatibility(
        adapter,
        required_members=required,
        expected_version=BRAIN_ADAPTER_INTERFACE_VERSION,
        contract_label=f"{normalized} adapter",
    )


def ensure_runner_compatibility(runner: Any) -> None:
    _ensure_contract_compatibility(
        runner,
        required_members=_RUNNER_REQUIRED_MEMBERS,
        expected_version=BRAIN_RUNNER_INTERFACE_VERSION,
        contract_label="runner",
    )
