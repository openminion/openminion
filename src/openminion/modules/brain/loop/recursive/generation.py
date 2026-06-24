from typing import Any

from .schemas import (
    EvidenceRef,
    MetaDirective,
    MemoryWriteIntent,
    RLMConstraints,
    RLMContinuation,
    RLMBudgets,
    RLMResponse,
    RLMTelemetry,
    RetrievalFilters,
    TaskState,
    TickTelemetry,
)


def generate(
    self,
    *,
    session_id: str,
    agent_id: str,
    purpose: str,
    query: str,
    ts: TaskState | dict[str, Any] | None = None,
    budgets: RLMBudgets | dict[str, Any] | None = None,
    constraints: RLMConstraints | dict[str, Any] | None = None,
    meta_directive: MetaDirective | dict[str, Any] | None = None,
    agent_policy: dict[str, Any] | None = None,
) -> RLMResponse:
    task_state = ts if isinstance(ts, TaskState) else TaskState.model_validate(ts or {})
    budget_cfg = (
        budgets
        if isinstance(budgets, RLMBudgets)
        else RLMBudgets.model_validate(budgets or self.config.budgets)
    )
    rlm_constraints = (
        constraints
        if isinstance(constraints, RLMConstraints)
        else RLMConstraints.model_validate(constraints or {})
    )
    meta = (
        meta_directive
        if isinstance(meta_directive, MetaDirective)
        else MetaDirective.model_validate(meta_directive or {})
    )

    if meta.max_ticks_override is not None:
        budget_cfg = budget_cfg.model_copy(
            update={
                "max_ticks": min(budget_cfg.max_ticks, int(meta.max_ticks_override))
            }
        )
    if meta.require_evidence:
        rlm_constraints = rlm_constraints.model_copy(
            update={"must_cite_evidence": True, "evidence_only": True}
        )
    if meta.verification_mode != "none":
        task_state = task_state.model_copy(
            update={"verification_mode": meta.verification_mode}
        )

    max_bad_streak = int(
        meta.max_bad_retrieval_streak or self.config.bad_retrieval_escalation_ticks
    )
    effective_policy = dict(self.config.default_agent_policy)
    if agent_policy:
        effective_policy.update(agent_policy)

    wm_state = self._load_wm_state(session_id=session_id)
    final_text = ""
    structured_output: dict[str, Any] | None = None
    final_json: dict[str, Any] | None = None
    evidence_refs: list[EvidenceRef] = []
    write_intents: list[MemoryWriteIntent] = []

    tick_reports: list[TickTelemetry] = []
    retrieval_stats: dict[str, int] = {
        "sm": 0,
        "em": 0,
        "skill": 0,
        "GOOD": 0,
        "OK": 0,
        "BAD": 0,
    }
    total_input_tokens = 0
    total_output_tokens = 0

    bad_retrieval_streak = 0
    max_bad_retrieval_streak_seen = 0
    stop_reason = "max_ticks_reached"
    current_query = query.strip() or "Continue with the current objective."
    continuation = RLMContinuation(
        needs_more_ticks=False, suggested_next_query=None, reason="completed"
    )

    for tick in range(1, budget_cfg.max_ticks + 1):
        phases: list[str] = []
        phases.append("LOAD_STATE")

        retrieval_strategy = self._resolve_retrieval_strategy(
            query=current_query,
            purpose=purpose,
            constraints=rlm_constraints,
        )
        target_k = max(1, int(self.config.retrieval.k_total))
        if meta.retrieval_cap_override is not None:
            target_k = min(target_k, int(meta.retrieval_cap_override))

        self._append_event(
            session_id=session_id,
            agent_id=agent_id,
            event_type="rlm.tick.started",
            payload={
                "tick_index": tick,
                "purpose": purpose,
                "query": current_query,
                "wm_version": wm_state.wm_version,
                "retrieval_strategy": retrieval_strategy,
                "retrieval_k": target_k,
            },
        )

        phases.append("RETRIEVE")
        retrieved = self.retrieve(
            session_id=session_id,
            agent_id=agent_id,
            query=current_query,
            k=target_k,
            purpose=purpose,
            strategy=retrieval_strategy,
            filters=RetrievalFilters(strategy=retrieval_strategy),
        )

        phases.append("EVALUATE_RETRIEVAL")
        retrieval_eval = self._evaluate_retrieval_quality(retrieved)
        retrieval_stats[retrieval_eval.quality] = (
            retrieval_stats.get(retrieval_eval.quality, 0) + 1
        )
        retrieval_stats["sm"] += sum(1 for item in retrieved if item.source == "sm")
        retrieval_stats["em"] += sum(1 for item in retrieved if item.source == "em")
        retrieval_stats["skill"] += sum(
            1 for item in retrieved if item.source == "skill"
        )

        gated_items = list(retrieved)
        used_empty_augmentation = False
        retrieval_action = retrieval_eval.action or "use"
        active_strategy = retrieval_strategy

        if meta.retrieve_only_if_good or task_state.retrieve_only_if_good:
            if retrieval_eval.quality != "GOOD":
                gated_items = []
                used_empty_augmentation = True
                retrieval_action = "meta_good_only_empty_augmentation"
        else:
            if retrieval_eval.quality == "OK":
                keep_n = max(
                    1,
                    min(
                        len(gated_items),
                        int(self.config.compression_extractive_max_blocks_ok),
                    ),
                )
                gated_items = gated_items[:keep_n]
                retrieval_action = "ok_reduce_evidence"
            elif retrieval_eval.quality == "BAD":
                alt_strategy = self._alternate_strategy(retrieval_strategy)
                if alt_strategy != retrieval_strategy:
                    alt_items = self.retrieve(
                        session_id=session_id,
                        agent_id=agent_id,
                        query=current_query,
                        k=target_k,
                        purpose=purpose,
                        strategy=alt_strategy,
                        filters=RetrievalFilters(strategy=alt_strategy),
                    )
                    alt_eval = self._evaluate_retrieval_quality(alt_items)
                    if alt_eval.quality in {"GOOD", "OK"}:
                        active_strategy = alt_strategy
                        retrieval_eval = alt_eval
                        gated_items = alt_items
                        retrieval_action = "bad_switch_strategy"

                if retrieval_eval.quality == "BAD":
                    if self.config.allow_empty_augmentation:
                        gated_items = []
                        used_empty_augmentation = True
                        retrieval_action = "bad_empty_augmentation"
                    else:
                        keep_n = max(
                            0,
                            int(self.config.compression_extractive_max_blocks_bad),
                        )
                        gated_items = gated_items[:keep_n] if keep_n > 0 else []
                        retrieval_action = "bad_minimal_context"

        if retrieval_eval.quality == "BAD":
            bad_retrieval_streak += 1
            max_bad_retrieval_streak_seen = max(
                max_bad_retrieval_streak_seen, bad_retrieval_streak
            )
        else:
            bad_retrieval_streak = 0

        phases.append("COMPRESS")
        compressed_items, compression_meta = self._compress_blocks(
            query=current_query,
            blocks=gated_items,
            retrieval_quality=retrieval_eval.quality,
            budgets=budget_cfg,
            constraints=rlm_constraints,
        )

        phases.append("PACK")
        context_messages, pack_hash = self._build_tick_messages(
            session_id=session_id,
            agent_id=agent_id,
            purpose=purpose,
            query=current_query,
            wm_state=wm_state,
            task_state=task_state,
            retrieved=compressed_items,
            max_prompt_tokens=budget_cfg.max_prompt_tokens,
        )

        phases.append("CALL")
        llm_result = self._call_llm(
            agent_id=agent_id,
            purpose=purpose,
            session_id=session_id,
            messages=context_messages,
            constraints=rlm_constraints,
            budgets=budget_cfg,
            task_state=task_state,
            agent_policy=effective_policy,
        )
        tick_output = self._parse_tick_output(
            llm_result=llm_result, fallback_query=current_query
        )

        usage_in, usage_out = self._extract_usage(
            llm_result=llm_result, prompt_messages=context_messages
        )
        total_input_tokens += usage_in
        total_output_tokens += usage_out

        phases.append("POSTPROCESS")
        final_text = tick_output.answer.strip() or final_text
        if tick_output.structured_output is not None:
            structured_output = tick_output.structured_output
        elif isinstance(llm_result.get("json_output"), dict):
            structured_output = llm_result.get("json_output")

        final_json = structured_output if structured_output is not None else final_json
        tick_evidence = self._normalize_evidence_refs(
            raw_refs=list(tick_output.evidence_refs) + list(tick_output.citations),
            source_hint="session",
        )
        evidence_refs = self._merge_evidence_refs(evidence_refs, tick_evidence)
        write_intents.extend(tick_output.memory_write_intents)

        wm_state = self._merge_wm(
            wm_state=wm_state,
            wm_patch=tick_output.wm_update,
            query=current_query,
            answer=tick_output.answer,
            max_items=self.config.wm_max_items_per_list,
            max_tool_summaries=self.config.wm_max_tool_summaries,
        )

        citation_coverage = self._estimate_citation_coverage(
            answer=tick_output.answer, evidence_count=len(tick_evidence)
        )

        phases.append("WRITEBACK")
        outputs_ref = self._write_episode_note(
            session_id=session_id,
            agent_id=agent_id,
            tick_index=tick,
            query=current_query,
            output=tick_output,
            retrieved=compressed_items,
            llm_status=str(llm_result.get("status") or ""),
            retrieval_quality=retrieval_eval.quality,
            retrieval_strategy=active_strategy,
            compression_meta=compression_meta,
        )
        if outputs_ref:
            evidence_refs = self._merge_evidence_refs(
                evidence_refs,
                self._normalize_evidence_refs([outputs_ref], source_hint="em"),
            )

        self._save_wm_state(
            session_id=session_id,
            wm_state=wm_state,
            task_state=task_state,
            reason=f"tick_{tick}",
        )

        staged_memory_refs = self._stage_memory_candidates(
            session_id=session_id,
            intents=tick_output.memory_write_intents,
            fallback_evidence=[item.ref_id for item in evidence_refs],
        )
        if staged_memory_refs:
            evidence_refs = self._merge_evidence_refs(
                evidence_refs,
                [
                    EvidenceRef(ref_type="memory", ref_id=item, source="sm")
                    for item in staged_memory_refs
                ],
            )

        phases.append("STOP_CHECK")
        next_query = (tick_output.next_query or "").strip() or current_query
        current_stop_reason = ""
        if tick_output.final and (
            not rlm_constraints.must_cite_evidence or bool(evidence_refs)
        ):
            current_stop_reason = "model_marked_final"
        elif bad_retrieval_streak >= max_bad_streak:
            current_stop_reason = "retrieval_quality_bad_streak"
        elif tick >= budget_cfg.max_ticks:
            current_stop_reason = "max_ticks_reached"

        stop = bool(current_stop_reason)
        if stop:
            stop_reason = current_stop_reason
            continuation = RLMContinuation(
                needs_more_ticks=stop_reason != "model_marked_final",
                suggested_next_query=None
                if stop_reason == "model_marked_final"
                else next_query,
                reason=stop_reason,
            )

        tick_payload = {
            "tick_index": tick,
            "pack_hash": pack_hash,
            "retrieval_strategy": active_strategy,
            "retrieval_k": target_k,
            "retrieval_quality": retrieval_eval.quality,
            "retrieval_action": retrieval_action,
            "retrieval_score_histogram": retrieval_eval.score_histogram,
            "retrieval_refs": [item.ref_id for item in compressed_items],
            "outputs_ref": outputs_ref,
            "llm_status": llm_result.get("status"),
            "compression": compression_meta,
            "token_usage": {"input_tokens": usage_in, "output_tokens": usage_out},
            "citation_coverage": citation_coverage,
            "used_empty_augmentation": used_empty_augmentation,
            "stop": stop,
            "stop_reason": stop_reason if stop else "",
        }
        self._append_event(
            session_id=session_id,
            agent_id=agent_id,
            event_type="rlm.tick.completed",
            payload=tick_payload,
            artifact_refs=[outputs_ref] if outputs_ref else None,
            memory_refs=staged_memory_refs or None,
            status="ok" if str(llm_result.get("status")) == "success" else "error",
        )

        tick_reports.append(
            TickTelemetry(
                tick_index=tick,
                phases=phases,
                retrieval_strategy=active_strategy,
                retrieval_k=target_k,
                retrieval_quality=retrieval_eval.quality,
                retrieval_action=retrieval_action,
                retrieval_score_histogram=retrieval_eval.score_histogram,
                retrieved_total=len(compressed_items),
                retrieved_sm=sum(1 for item in compressed_items if item.source == "sm"),
                retrieved_em=sum(1 for item in compressed_items if item.source == "em"),
                retrieved_skill=sum(
                    1 for item in compressed_items if item.source == "skill"
                ),
                selected_unit_kinds=sorted(
                    {item.unit_kind for item in compressed_items}
                ),
                selected_raptor_levels=sorted(
                    {
                        item.raptor_level
                        for item in compressed_items
                        if item.raptor_level != "none"
                    }
                ),
                compression_method=str(compression_meta.get("method_id", "")),
                compression_ratio=float(compression_meta.get("ratio", 1.0) or 1.0),
                compression_input_tokens=int(
                    compression_meta.get("input_tokens", 0) or 0
                ),
                compression_output_tokens=int(
                    compression_meta.get("output_tokens", 0) or 0
                ),
                used_empty_augmentation=used_empty_augmentation,
                pack_hash=pack_hash,
                llm_status=str(llm_result.get("status") or ""),
                input_tokens=usage_in,
                output_tokens=usage_out,
                citation_coverage=citation_coverage,
                stop=stop,
                stop_reason=stop_reason if stop else "",
            )
        )

        if stop:
            break
        current_query = next_query

    telemetry = RLMTelemetry(
        ticks_used=len(tick_reports),
        stop_reason=stop_reason,
        retrieval_stats=retrieval_stats,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        max_bad_retrieval_streak=max_bad_retrieval_streak_seen,
        tick_reports=tick_reports,
    )

    return RLMResponse(
        final_text=final_text,
        structured_output=structured_output,
        final_json=final_json,
        evidence_refs=evidence_refs,
        memory_write_intents=write_intents,
        wm_update=wm_state,
        telemetry=telemetry,
        continuation=continuation,
    )
