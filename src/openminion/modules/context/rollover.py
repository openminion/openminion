from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, runtime_checkable


SEGMENT_ORDER = [
    "static_prefix",
    "mission_snapshot",
    "seed_block",
    "summaries",
    "recent_window",
    "retrieval",
    "evidence_refs",
    "turn_input",
]


def sort_segments_by_position(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort segments according to canonical position-aware ordering."""
    order_map = {name: i for i, name in enumerate(SEGMENT_ORDER)}
    return sorted(
        segments,
        key=lambda s: (
            order_map.get(s.get("bucket", s.get("id", "")), 99),
            s.get("id", ""),
        ),
    )


@dataclass
class ValuePerTokenBudget:
    """Budget allocation based on value-per-token scoring."""

    bucket: str
    base_cap: int
    value_score: float = 1.0
    adjusted_cap: int = 0

    def __post_init__(self):
        if self.adjusted_cap == 0:
            self.adjusted_cap = self.base_cap


def allocate_value_budgets(
    buckets: list[ValuePerTokenBudget],
    total_budget: int,
    *,
    min_per_bucket: int = 50,
) -> list[ValuePerTokenBudget]:
    """Allocate token budgets proportional to value scores."""
    total_value = sum(b.value_score for b in buckets) or 1.0
    for b in buckets:
        share = b.value_score / total_value
        b.adjusted_cap = max(min_per_bucket, int(total_budget * share))
    total_allocated = sum(b.adjusted_cap for b in buckets)
    if total_allocated > total_budget:
        scale = total_budget / total_allocated
        for b in buckets:
            b.adjusted_cap = max(min_per_bucket, int(b.adjusted_cap * scale))
    return buckets


class RolloverTrigger:
    """Evaluates whether a prompt-context rollover should happen."""

    TOKEN_PRESSURE = "token_pressure"
    EXPLICIT_REQUEST = "explicit_request"
    TASK_BOUNDARY = "task_boundary"
    CHECKPOINT_AGE = "checkpoint_age"

    def __init__(
        self,
        *,
        pressure_threshold: float = 0.90,
        max_events_without_checkpoint: int = 100,
    ) -> None:
        self.pressure_threshold = pressure_threshold
        self.max_events_without_checkpoint = max_events_without_checkpoint

    def evaluate(
        self,
        *,
        prompt_tokens: int = 0,
        budget_tokens: int = 0,
        events_since_checkpoint: int = 0,
        explicit_request: bool = False,
        at_task_boundary: bool = False,
    ) -> list[str]:
        """Return list of rollover reasons that fired."""
        reasons: list[str] = []
        if explicit_request:
            reasons.append(self.EXPLICIT_REQUEST)
        if budget_tokens > 0 and prompt_tokens > 0:
            if prompt_tokens / budget_tokens >= self.pressure_threshold:
                reasons.append(self.TOKEN_PRESSURE)
        if events_since_checkpoint >= self.max_events_without_checkpoint:
            reasons.append(self.CHECKPOINT_AGE)
        if at_task_boundary:
            reasons.append(self.TASK_BOUNDARY)
        return reasons


@runtime_checkable
class SessionStore(Protocol):
    """Session store protocol for rollover orchestration."""

    def create_prompt_context(self, session_id: str, **kw) -> str: ...
    def close_prompt_context(self, prompt_context_id: str, **kw) -> None: ...
    def get_active_prompt_context(self, session_id: str) -> Optional[dict]: ...
    def emit_canonical_event(
        self, session_id: str, event_type: str, payload: Optional[dict] = None, **kw
    ) -> str: ...


@runtime_checkable
class CompactionStore(Protocol):
    """Compaction service protocol for seed generation."""

    def checkpoint(self, session_id: str, **kw) -> str: ...
    def build_rollover_seed(self, session_id: str, **kw) -> Any: ...


class RolloverOrchestrator:
    """Coordinate checkpoint, seed, and prompt-context rollover steps."""

    def __init__(
        self,
        *,
        sessctl: Any = None,
        compressor: Any = None,
        trigger: Optional[RolloverTrigger] = None,
    ) -> None:
        self._sessctl = sessctl
        self._compressor = compressor
        self._trigger = trigger or RolloverTrigger()
        self._last_rollover: Dict[str, dict] = {}

    def maybe_rollover(
        self,
        session_id: str,
        *,
        prompt_tokens: int = 0,
        budget_tokens: int = 0,
        events_since_checkpoint: int = 0,
        explicit_request: bool = False,
        at_task_boundary: bool = False,
    ) -> dict[str, Any]:
        """Run rollover when any trigger fires and return the result payload."""
        reasons = self._trigger.evaluate(
            prompt_tokens=prompt_tokens,
            budget_tokens=budget_tokens,
            events_since_checkpoint=events_since_checkpoint,
            explicit_request=explicit_request,
            at_task_boundary=at_task_boundary,
        )
        if not reasons:
            return {
                "rolled_over": False,
                "reasons": [],
                "seed_text": None,
                "new_prompt_context_id": None,
            }

        return self.execute_rollover(session_id, reasons=reasons)

    def execute_rollover(
        self,
        session_id: str,
        *,
        reasons: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Checkpoint, build a seed, and swap prompt contexts."""
        reasons = reasons or ["explicit_request"]
        reason_str = "|".join(reasons)

        checkpoint_id = None
        if self._compressor and hasattr(self._compressor, "checkpoint"):
            checkpoint_id = self._compressor.checkpoint(session_id, reason=reason_str)

        seed_text = ""
        seed_bundle_id = None
        if self._compressor and hasattr(self._compressor, "build_rollover_seed"):
            seed = self._compressor.build_rollover_seed(session_id)
            seed_text = (
                seed.render_text() if hasattr(seed, "render_text") else str(seed)
            )
            seed_bundle_id = getattr(seed, "seed_id", None)

        old_pc_id = None
        if self._sessctl and hasattr(self._sessctl, "get_active_prompt_context"):
            active = self._sessctl.get_active_prompt_context(session_id)
            if active:
                old_pc_id = active["prompt_context_id"]
                self._sessctl.close_prompt_context(
                    old_pc_id, rollover_reason=reason_str
                )

        new_pc_id = None
        if self._sessctl and hasattr(self._sessctl, "create_prompt_context"):
            new_pc_id = self._sessctl.create_prompt_context(
                session_id,
                seed_bundle_id=seed_bundle_id,
                checkpoint_id=checkpoint_id,
            )

        if self._sessctl and hasattr(self._sessctl, "emit_canonical_event"):
            self._sessctl.emit_canonical_event(
                session_id,
                "context.rollover",
                {
                    "old_prompt_context_id": old_pc_id,
                    "new_prompt_context_id": new_pc_id,
                    "checkpoint_id": checkpoint_id,
                    "seed_bundle_id": seed_bundle_id,
                    "reasons": reasons,
                },
            )

        result = {
            "rolled_over": True,
            "reasons": reasons,
            "seed_text": seed_text,
            "new_prompt_context_id": new_pc_id,
            "checkpoint_id": checkpoint_id,
            "seed_bundle_id": seed_bundle_id,
            "old_prompt_context_id": old_pc_id,
        }
        self._last_rollover[session_id] = result
        return result

    def get_last_rollover(self, session_id: str) -> Optional[dict[str, Any]]:
        """Return the most recent rollover result for a session."""
        return self._last_rollover.get(session_id)


class RecallAPI:
    """Read structured sections back out of a rendered seed bundle."""

    def __init__(self, seed_sections: Optional[list[dict[str, Any]]] = None) -> None:
        self._sections = seed_sections or []
        self._index: Dict[str, list[dict]] = {}
        for sec in self._sections:
            sec_type = sec.get("section_type", "unknown")
            self._index.setdefault(sec_type, []).append(sec)

    @classmethod
    def from_seed_text(cls, seed_text: str) -> "RecallAPI":
        """Parse a rendered seed text into structured sections."""
        sections = []
        current_type = "unknown"
        current_lines: list[str] = []
        for line in seed_text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if current_lines:
                    sections.append(
                        {
                            "section_type": current_type.lower(),
                            "text": "\n".join(current_lines),
                        }
                    )
                    current_lines = []
                current_type = stripped[1:-1].lower()
            else:
                if stripped:
                    current_lines.append(stripped)
        if current_lines:
            sections.append(
                {
                    "section_type": current_type,
                    "text": "\n".join(current_lines),
                }
            )
        return cls(sections)

    def get_decisions(self) -> list[str]:
        """Return all prior decisions."""
        return [s["text"] for s in self._index.get("decisions", [])]

    def get_constraints(self) -> list[str]:
        """Return all prior constraints."""
        return [s["text"] for s in self._index.get("constraints", [])]

    def get_summary(self) -> str:
        """Return the prior context summary."""
        summaries = self._index.get("summary", [])
        return summaries[0]["text"] if summaries else ""

    def get_entities(self) -> list[str]:
        """Return known entities from prior context."""
        entities = self._index.get("entities", [])
        if not entities:
            return []
        return [e.strip() for e in entities[0]["text"].split("\n") if e.strip()]

    def get_open_loops(self) -> list[str]:
        """Return open loops/tasks from prior context."""
        loops = self._index.get("open_loops", [])
        if not loops:
            return []
        return [line.strip() for line in loops[0]["text"].split("\n") if line.strip()]

    def has_section(self, section_type: str) -> bool:
        return section_type in self._index

    def all_section_types(self) -> list[str]:
        return list(self._index.keys())


def sanitize_tool_output_for_context(
    tool_name: str,
    raw_output: str,
    *,
    max_tokens: int = 150,
    strip_binary: bool = True,
) -> str:
    """Strip unsafe markers and cap tool output before prompt injection."""
    if strip_binary:
        raw_output = "".join(c for c in raw_output if c.isprintable() or c in "\n\t ")

    # Filter a small set of provider/control markers before prompt injection.
    injection_markers = ["[SYSTEM]", "[INST]", "<<SYS>>", "</s>", "<|im_start|>"]
    for marker in injection_markers:
        raw_output = raw_output.replace(marker, f"[{tool_name}:filtered]")

    words = raw_output.split()
    if len(words) > max_tokens:
        return " ".join(words[:max_tokens]) + f"... [{tool_name}: truncated]"
    return raw_output


def compute_prefix_hash(segments: list[dict[str, Any]]) -> str:
    """Compute a stable hash for cacheable prefix segments."""
    prefix_segments = [
        s
        for s in segments
        if s.get("bucket") == "static_prefix" or s.get("id") == "static_prefix"
    ]
    content = "".join(s.get("content", "") for s in prefix_segments)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def check_prefix_stability(
    current_hash: str,
    previous_hash: str,
) -> dict[str, Any]:
    """Check if the static prefix has remained stable across assemblies."""
    stable = current_hash == previous_hash
    return {
        "stable": stable,
        "current_hash": current_hash,
        "previous_hash": previous_hash,
        "warning": None if stable else "prefix_changed: KV-cache invalidated",
    }


def inject_seed_into_segments(
    segments: list[dict[str, Any]],
    seed_text: str,
    *,
    max_seed_tokens: int = 1200,
) -> list[dict[str, Any]]:
    """Insert rendered seed text between mission snapshot and summaries."""
    if not seed_text or not seed_text.strip():
        return segments

    words = seed_text.split()
    if len(words) > max_seed_tokens:
        seed_text = " ".join(words[:max_seed_tokens])

    seed_segment = {
        "id": "seed_block",
        "bucket": "summaries",
        "role": "system",
        "content": f"[CONTEXT SEED]\n{seed_text}",
        "token_estimate": len(seed_text.split()),
        "pinned": False,
        "is_cacheable": False,
    }

    # Keep the seed after mission framing and before summary/retrieval content.
    insert_idx = 0
    for i, seg in enumerate(segments):
        bucket = seg.get("bucket", seg.get("id", ""))
        if bucket in ("static_prefix", "mission_snapshot"):
            insert_idx = i + 1

    result = list(segments)
    result.insert(insert_idx, seed_segment)
    return result


def build_cache_key(
    agent_id: str,
    model_hint: str,
    prefix_hash: str,
    *,
    purpose: str = "act",
) -> str:
    """Build a deterministic cache key for provider KV-cache reuse."""
    data = f"{agent_id}|{model_hint}|{prefix_hash}|{purpose}"
    return hashlib.sha256(data.encode()).hexdigest()[:24]


def should_use_cached_prefix(
    model_hint: str,
    prefix_hash: str,
    *,
    known_cache_prefixes: Optional[Dict[str, str]] = None,
) -> bool:
    """Determine if the current prefix matches a known cached prefix."""
    if not known_cache_prefixes:
        return False
    cached = known_cache_prefixes.get(model_hint)
    return cached == prefix_hash
