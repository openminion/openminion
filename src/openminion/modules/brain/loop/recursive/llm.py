import asyncio
import inspect
import json
from typing import Any

from .schemas import (
    RetrievedContext,
    RetrievalQuality,
    RLMBudgets,
    RLMConstraints,
    TaskState,
    TickOutput,
    WMState,
)
from .payloads import _estimate_tokens, _stable_hash

try:
    from openminion.modules.context.schemas import (
        BuildPackRequest as _CtxBuildPackRequest,
    )
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    _CtxBuildPackRequest = None  # type: ignore[assignment]


def _compress_blocks(
    self,
    *,
    query: str,
    blocks: list[RetrievedContext],
    retrieval_quality: RetrievalQuality,
    budgets: RLMBudgets,
    constraints: RLMConstraints,
) -> tuple[list[RetrievedContext], dict[str, Any]]:
    if self._compressctl is not None:
        external = self._compress_with_external(
            query=query,
            blocks=blocks,
            retrieval_quality=retrieval_quality,
            budgets=budgets,
            constraints=constraints,
        )
        if external is not None:
            return external

    in_tokens = sum(_estimate_tokens(item.text) for item in blocks)
    if not blocks:
        return [], {
            "method_id": f"{self.config.compression_method_id}.empty",
            "ratio": 1.0,
            "input_tokens": in_tokens,
            "output_tokens": 0,
        }

    if retrieval_quality == "GOOD":
        max_blocks = self.config.compression_extractive_max_blocks_good
    elif retrieval_quality == "OK":
        max_blocks = self.config.compression_extractive_max_blocks_ok
    else:
        max_blocks = self.config.compression_extractive_max_blocks_bad

    selected = list(blocks[: max(0, int(max_blocks))])
    if not selected:
        return [], {
            "method_id": f"{self.config.compression_method_id}.none",
            "ratio": 1.0,
            "input_tokens": in_tokens,
            "output_tokens": 0,
        }

    target_tokens = max(80, int(budgets.max_prompt_tokens * 0.35))
    output_blocks: list[RetrievedContext] = []
    used_tokens = 0
    for item in selected:
        remaining = target_tokens - used_tokens
        if remaining <= 0:
            break
        max_chars = remaining * 4
        text = item.text.strip()
        clipped = text[:max_chars].rstrip()
        if not clipped:
            continue
        output_blocks.append(item.model_copy(update={"text": clipped}))
        used_tokens += _estimate_tokens(clipped)

    out_tokens = sum(_estimate_tokens(item.text) for item in output_blocks)
    ratio = float(out_tokens) / float(max(1, in_tokens))
    return output_blocks, {
        "method_id": f"{self.config.compression_method_id}.extractive",
        "ratio": ratio,
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
    }


def _compress_with_external(
    self,
    *,
    query: str,
    blocks: list[RetrievedContext],
    retrieval_quality: RetrievalQuality,
    budgets: RLMBudgets,
    constraints: RLMConstraints,
) -> tuple[list[RetrievedContext], dict[str, Any]] | None:
    if self._compressctl is None:
        return None

    block_payload = [item.model_dump(mode="json") for item in blocks]
    policy = {
        "extractive_only": True,
        "allow_empty": True,
        "retrieval_quality": retrieval_quality,
        "must_cite_evidence": constraints.must_cite_evidence,
    }
    budget_payload = {
        "max_prompt_tokens": budgets.max_prompt_tokens,
        "max_output_tokens": budgets.max_output_tokens,
    }

    try:
        raw = self._compressctl.compress(
            blocks=block_payload, query=query, budgets=budget_payload, policy=policy
        )
    except TypeError:
        try:
            raw = self._compressctl.compress(
                block_payload, query, budget_payload, policy
            )  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None

    row = self._to_plain(raw)
    compressed_blocks_raw = (
        row.get("blocks") if isinstance(row.get("blocks"), list) else []
    )
    compressed_blocks = self._normalize_retrieval_rows(
        compressed_blocks_raw, strategy="auto"
    )

    if not compressed_blocks and blocks:
        for item in blocks:
            compressed_blocks.append(item)
            if (
                len(compressed_blocks)
                >= self.config.compression_extractive_max_blocks_ok
            ):
                break

    in_tokens = sum(_estimate_tokens(item.text) for item in blocks)
    out_tokens = sum(_estimate_tokens(item.text) for item in compressed_blocks)
    ratio = float(out_tokens) / float(max(1, in_tokens))

    meta = {
        "method_id": str(row.get("method_id", "compressctl")),
        "ratio": float(row.get("ratio", ratio) or ratio),
        "input_tokens": int(row.get("input_tokens", in_tokens) or in_tokens),
        "output_tokens": int(row.get("output_tokens", out_tokens) or out_tokens),
    }
    return compressed_blocks, meta


def _build_tick_messages(
    self,
    *,
    session_id: str,
    agent_id: str,
    purpose: str,
    query: str,
    wm_state: WMState,
    task_state: TaskState,
    retrieved: list[RetrievedContext],
    max_prompt_tokens: int,
) -> tuple[list[dict[str, str]], str]:
    request_payload = {
        "session_id": session_id,
        "agent_id": agent_id,
        "purpose": purpose,
        "query": query,
        "constraints": {},
    }
    request_obj: Any = request_payload
    if _CtxBuildPackRequest is not None:
        request_obj = _CtxBuildPackRequest.model_validate(request_payload)
    try:
        pack = self._contextctl.build_pack(request_obj)
    except TypeError:
        pack = self._contextctl.build_pack(request_payload)

    pack_messages = self._normalize_messages(pack)
    pack_hash = self._extract_pack_hash(pack, pack_messages)

    wm_lines = [
        "[WORKING MEMORY]",
        f"objective: {wm_state.objective}",
        f"current_step: {wm_state.current_step or ''}",
        f"step_cursor: {wm_state.step_cursor or ''}",
    ]
    if wm_state.constraints:
        wm_lines.append("constraints: " + "; ".join(wm_state.constraints))
    if wm_state.invariants:
        wm_lines.append("invariants: " + "; ".join(wm_state.invariants))
    if wm_state.key_decisions:
        wm_lines.append("decisions: " + "; ".join(wm_state.key_decisions))
    if wm_state.open_questions:
        wm_lines.append("open_questions: " + "; ".join(wm_state.open_questions))
    if wm_state.must_not_forget:
        wm_lines.append("must_not_forget: " + "; ".join(wm_state.must_not_forget))
    wm_lines.append("[TASK STATE]")
    wm_lines.append(f"plan_id: {task_state.plan_id or ''}")
    wm_lines.append(f"step_id: {task_state.step_id or ''}")
    wm_lines.append(f"retry_count: {task_state.retry_count}")
    wm_lines.append(f"verification_mode: {task_state.verification_mode}")

    retrieval_lines = ["[COMPRESSED AUGMENTATION]"]
    for idx, item in enumerate(retrieved, start=1):
        text = item.text.strip().replace("\n", " ")
        retrieval_lines.append(
            f"{idx}. ({item.source}/{item.unit_kind}) {item.ref_id} :: {text[:280]}"
        )
    if len(retrieval_lines) == 1:
        retrieval_lines.append("(empty augmentation)")

    controller_prompt = "\n".join(wm_lines + [""] + retrieval_lines).strip()
    controller_msg = {"role": "system", "content": controller_prompt}

    merged = [controller_msg] + pack_messages
    while (
        sum(_estimate_tokens(item["content"]) for item in merged) > max_prompt_tokens
        and len(merged) > 2
    ):
        del merged[1]
    return merged, pack_hash


def _normalize_messages(self, pack: Any) -> list[dict[str, str]]:
    raw_messages: list[Any]
    if isinstance(pack, dict):
        raw_messages = list(pack.get("messages", []))
    else:
        raw_messages = list(getattr(pack, "messages", []) or [])

    out: list[dict[str, str]] = []
    for item in raw_messages:
        if isinstance(item, dict):
            role = str(item.get("role", "user"))
            content = str(item.get("content", ""))
        else:
            role = str(getattr(item, "role", "user"))
            content = str(getattr(item, "content", ""))
        role = role if role in {"system", "user", "assistant", "tool"} else "user"
        out.append({"role": role, "content": content})

    if not out:
        out = [{"role": "user", "content": ""}]
    return out


def _extract_pack_hash(self, pack: Any, messages: list[dict[str, str]]) -> str:
    raw = (
        pack.get("pack_version")
        if isinstance(pack, dict)
        else getattr(pack, "pack_version", None)
    )
    if raw:
        return str(raw)
    return _stable_hash(messages)


def _call_llm(
    self,
    *,
    agent_id: str,
    purpose: str,
    session_id: str,
    messages: list[dict[str, str]],
    constraints: RLMConstraints,
    budgets: RLMBudgets,
    task_state: TaskState,
    agent_policy: dict[str, Any],
) -> dict[str, Any]:
    request = {
        "purpose": purpose,
        "messages": messages,
        "output_schema": constraints.output_schema,
        "constraints": {
            "evidence_only": constraints.evidence_only,
            "must_cite_evidence": constraints.must_cite_evidence,
            "risk_level": constraints.risk_level,
            "verification_mode": constraints.verification_mode,
            "self_reflect": constraints.self_reflect,
        },
        "budget": {
            "timeout_ms": budgets.timeout_ms,
            "max_tokens": budgets.max_output_tokens,
        },
        "trace": {
            "session_id": session_id,
            "agent_id": agent_id,
            "task_id": task_state.plan_id,
        },
        "metadata": {"rlm": {"service": "openminion-rlm.v1"}},
    }

    result = self._llmctl.call_for_agent(agent_id, purpose, request, agent_policy)
    if inspect.isawaitable(result):
        result = self._run_awaitable(result)
    payload = self._to_plain(result)

    if "status" in payload and "text" in payload:
        return payload

    if isinstance(payload.get("candidates"), list):
        chosen = self._pick_ensemble_candidate(payload)
        return chosen

    return {
        "status": "failed",
        "text": "",
        "error": {"code": "INVALID_RESPONSE", "details": payload},
    }


def _run_awaitable(self, awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError(
        "RLMService cannot await llm call_for_agent while an event loop is already running"
    )


def _pick_ensemble_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
    candidates = (
        payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    )
    winner_id = None
    selection = payload.get("selection")
    if isinstance(selection, dict):
        winner_id = selection.get("winner_candidate_id")
    if winner_id:
        for item in candidates:
            if isinstance(item, dict) and str(item.get("candidate_id")) == str(
                winner_id
            ):
                return item
    for item in candidates:
        if isinstance(item, dict) and str(item.get("status")) == "success":
            return item
    if candidates and isinstance(candidates[0], dict):
        return candidates[0]
    return {"status": "failed", "text": "", "error": {"code": "NO_CANDIDATES"}}


def _parse_tick_output(
    self, *, llm_result: dict[str, Any], fallback_query: str
) -> TickOutput:
    json_output = llm_result.get("json")
    if not isinstance(json_output, dict):
        json_output = llm_result.get("json_output")
    if not isinstance(json_output, dict):
        json_output = self._extract_json_dict(str(llm_result.get("text") or ""))

    if isinstance(json_output, dict):
        payload = dict(json_output)
        if "answer" not in payload:
            payload["answer"] = str(llm_result.get("text") or "")
        if "next_query" not in payload and not payload.get("final"):
            payload["next_query"] = fallback_query
        try:
            return TickOutput.model_validate(payload)
        except Exception:  # noqa: BLE001
            pass

    text = str(llm_result.get("text") or "").strip()
    return TickOutput(
        final=True,
        answer=text,
        next_query=fallback_query,
        episode_note=text[:600],
        evidence_refs=[],
        citations=[],
        wm_update={},
        memory_write_intents=[],
    )


def _extract_json_dict(self, text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None

    candidates = [raw]
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            candidates.append("\n".join(lines[1:-1]))
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(raw[start : end + 1])

    for item in candidates:
        try:
            parsed = json.loads(item)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_usage(
    self, *, llm_result: dict[str, Any], prompt_messages: list[dict[str, str]]
) -> tuple[int, int]:
    usage = llm_result.get("usage")
    if isinstance(usage, dict):
        in_tokens = int(usage.get("input_tokens", 0) or 0)
        out_tokens = int(usage.get("output_tokens", 0) or 0)
        if in_tokens > 0 or out_tokens > 0:
            return in_tokens, out_tokens

    estimated_in = sum(
        _estimate_tokens(item.get("content", "")) for item in prompt_messages
    )
    estimated_out = _estimate_tokens(str(llm_result.get("text") or ""))
    return estimated_in, estimated_out
