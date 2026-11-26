from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import time

import pytest

from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.assertions import assert_exact_reply, visible_text

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(600)]


def test_live_focus_memory_survives_distinct_session(focus_probe: FocusProbe) -> None:
    require_live_focus()
    token = "MLQC-FOCUS-CONTINUITY-7B4E"
    base_probe = focus_probe.for_workdir(
        focus_probe.workdir,
        include_project_context=False,
    )
    teach_probe = base_probe.for_session(f"{base_probe.session_id}-teach")
    teach_prompt = (
        "Use memory.write to store this in persistent agent memory with "
        'record_type="fact" and key="fact:continuity_code". Omit scope so the '
        f"active agent scope is used. The exact continuity code is {token}. "
        "After the tool succeeds, reply with exactly: stored"
    )
    with teach_probe.session() as teach_session:
        teach_probe.wait_ready(teach_session)
        teach_transcript = teach_probe.run_turn(
            teach_session,
            FocusScenario(
                scenario_id="memory_continuity_teach",
                prompt=teach_prompt,
                expected_markers=("stored",),
                requires_approval=True,
                timeout=240,
                include_project_context=False,
            ),
        )
        assert_exact_reply(teach_transcript, teach_prompt, "stored")
        memory_report = teach_probe.run_slash(
            teach_session,
            "/memory",
            marker="Memory:",
        )
        assert "Memory:" in visible_text(memory_report)

    recall_probe = base_probe.for_session(f"{base_probe.session_id}-recall")
    recall_prompt = (
        "What is the exact continuity code I asked you to remember? "
        "Reply with only the code."
    )
    with recall_probe.session() as recall_session:
        recall_probe.wait_ready(recall_session)
        transcript = recall_probe.run_turn(
            recall_session,
            FocusScenario(
                scenario_id="memory_continuity_recall",
                prompt=recall_prompt,
                expected_markers=(token,),
                timeout=300,
                include_project_context=False,
            ),
        )
    assert_exact_reply(transcript, recall_prompt, token)


@pytest.mark.timeout(23000)
def test_live_focus_memory_campaign(request: pytest.FixtureRequest) -> None:
    """Run only with a hash-bound Gate B approval, separate from baseline smoke."""
    from tests.e2e.cli.focus.harness.memory_campaign import (
        PHASES,
        campaign_config,
        load_campaign,
        read_state,
        run_phase,
    )

    require_live_focus()
    manifest = load_campaign(Path(os.environ["CSMA_MANIFEST"]))
    credentials = json.loads(Path(manifest["credential_profile_path"]).read_text())
    key = os.environ.get("MINIMAX_API_KEY") or credentials.get("runtime", {}).get(
        "env", {}
    ).get("MINIMAX_API_KEY")
    if not key or key.startswith("${"):
        raise ValueError("MiniMax credential unavailable; no request sent")
    os.environ["MINIMAX_API_KEY"] = key
    corpus = json.loads(Path(manifest["corpus_path"]).read_text())
    run_root = Path(manifest["run_root"])
    run_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    evaluator = run_root / "evaluator"
    evaluator.mkdir(mode=0o700)
    agents_root = run_root / "agents"
    agents_root.mkdir(mode=0o700)
    request.addfinalizer(lambda: shutil.rmtree(agents_root))
    started = time.monotonic()
    (run_root / "started.json").write_text(json.dumps({"monotonic": started}))
    for case, phases in PHASES.items():
        for pair in range(1, 6):
            pair_id = f"{case}-{pair:02d}"
            tokens = corpus[pair_id]
            hashes = {
                label: hashlib.sha256(token.encode()).hexdigest()
                for label, token in tokens.items()
            }
            for arm in ("memory", "control") if pair % 2 else ("control", "memory"):
                arm_root = run_root / "agents" / f"{pair_id}-{arm}"
                workdir = arm_root / "work"
                workdir.mkdir(mode=0o700, parents=True)
                data_root = arm_root / "data"
                data_root.mkdir(mode=0o700)
                config, _ = campaign_config(manifest, f"{pair_id}-{arm}-teach")
                config_path = arm_root / "config.json"
                config_path.write_text(json.dumps(config))
                config_path.chmod(0o600)
                control_path = arm_root / "control.json"
                config["runtime"]["memory_enabled"] = False
                control_path.write_text(json.dumps(config))
                control_path.chmod(0o600)
                original_id = None
                producer_states = []
                for phase in phases:
                    slot = f"{pair_id}-{arm}-{phase}"
                    prompt = _campaign_prompt(case, phase, tokens, original_id)
                    selected = (
                        control_path
                        if arm == "control" and phase == "recall"
                        else config_path
                    )
                    report = {"slot": slot, "status": "incomplete"}
                    if phase == "recall":
                        report["pre_consumer_inventory"] = list(producer_states)
                    try:
                        if time.monotonic() - started >= manifest["elapsed_seconds"]:
                            raise AssertionError("campaign_lifetime_exhausted")
                        answer, requests = run_phase(
                            manifest=manifest,
                            slot=slot,
                            config_path=selected,
                            data_root=data_root,
                            workdir=workdir,
                            evaluator_root=evaluator,
                            prompt=prompt,
                            target_hashes=hashes,
                            deadline_monotonic=started
                            + manifest["max_admission_seconds"],
                        )
                        state = read_state(config_path, data_root, slot, tokens)
                        report.update(state)
                        outcomes, original_id = _phase_outcomes(
                            case,
                            phase,
                            arm,
                            tokens,
                            original_id,
                            answer,
                            requests,
                            state,
                            producer_states=producer_states,
                        )
                        report["outcomes"] = outcomes
                        report["status"] = (
                            "passed" if all(outcomes.values()) else "failed"
                        )
                        if report["status"] != "passed":
                            raise AssertionError("qualification_failed")
                        producer_states.append(state)
                    except Exception:
                        report["failure_code"] = "phase_failed"
                        raise AssertionError(
                            f"{slot}: phase_failed; no replacement"
                        ) from None
                    finally:
                        (evaluator / f"{slot}.result.json").write_text(
                            json.dumps(report, indent=2)
                        )
                        # Only content-free evaluator evidence survives a failed arm.
                        if report["status"] != "passed" or phase == phases[-1]:
                            shutil.rmtree(arm_root)


def _phase_outcomes(
    case,
    phase,
    arm,
    tokens,
    original_id,
    answer,
    requests,
    state,
    *,
    producer_states=(),
):
    active = [row for row in state["records"] if row["active"]]
    target = "new" if case == "correction" and phase != "teach" else "old"
    covered = [
        row["id"]
        for row in active
        if row["continuity_key"] and target in row["matches"]
    ]
    sent = [row for row in requests if row.get("event") == "request"]
    outcomes = {
        "response_present": bool(state["response_ids"]),
        "requests_present": bool(sent),
        "recorded_answer_matches": hashlib.sha256(answer.encode()).hexdigest()
        in state["response_hashes"],
    }
    if phase == "teach" and case != "automatic":
        outcomes["unique_public_write"] = len(covered) == 1
        original_id = covered[0] if len(covered) == 1 else None
        if case == "relevance":
            outcomes["distractor_stored"] = any(
                "distractor" in row["matches"] and row["weather_key"] for row in active
            )
    if case == "automatic" and phase == "teach":
        outcomes["no_explicit_write_attempt"] = not state["memory_write_events"]
        candidates = [row for row in state["candidates"] if "old" in row["matches"]]
        outcomes["capture_lineage"] = bool(candidates) and all(
            row["root_turn_id"]
            and any(
                row["capture_id"] == receipt["capture_id"]
                and receipt["disposition"] == "succeeded"
                and row["id"] in receipt["output_ids"]
                for receipt in state["capture"]
            )
            for row in candidates
        )
        # A separate session-summary copy is not candidate admission.
        outcomes["not_admitted"] = (
            bool(candidates)
            and all(row["status"] == "proposed" for row in candidates)
            and not any(
                row["type"] == "fact" and "old" in row["matches"] for row in active
            )
        )
    if phase in {"correct", "forget"}:
        outcomes["old_record_inactive"] = not any(
            row["id"] == original_id for row in active
        )
        if phase == "correct":
            outcomes["replacement_active"] = len(covered) == 1
    if phase == "recall":
        wants_value = arm == "memory" and case in {
            "explicit",
            "correction",
            "relevance",
        }
        exposed = {label for row in sent for label in row["target_matches"]}
        forbidden = set(tokens) - ({target} if wants_value else set())
        outcomes.update(
            answer_correct=answer == (tokens[target] if wants_value else "UNKNOWN"),
            no_forbidden_exposure=not bool(exposed & forbidden),
            required_exposure=not wants_value or target in exposed,
        )
        if case == "forget":
            outcomes["forget_inventory_present"] = bool(producer_states)
            # Earlier record snapshots precede the forget operation; use its readback.
            outcomes["forget_clean_fixture"] = (
                bool(producer_states)
                and not any(
                    row["active"] and "old" in row["matches"]
                    for row in producer_states[-1]["records"]
                )
                and all(
                    "old" not in row["matches"]
                    or row["eligibility"]
                    in {"protected_history", "session_local", "resume_only"}
                    or (
                        row["kind"] == "episode"
                        and row["scope_key"]
                        in {
                            f"session:{snapshot['session_id']}"
                            for snapshot in producer_states
                        }
                    )
                    for row in producer_states[-1]["retained_carriers"]
                )
            )
        manifests = state["context_manifests"]
        observed_ids = {row["canonical"].get("llm_call_id") for row in sent}
        outcomes["native_context_join"] = bool(manifests) and all(
            row["llm_call_id"] and row["llm_call_id"] in observed_ids
            for row in manifests
        )
        if wants_value:
            outcomes["target_context_join"] = any(
                row["canonical"].get("llm_call_id") == context["llm_call_id"]
                and target in row["target_matches"]
                and any(
                    record_id in (context[field] or [])
                    for record_id in covered
                    for field in (
                        "recalled_memory",
                        "facts",
                        "memory",
                        "recent_session_artifacts",
                    )
                )
                for row in sent
                for context in manifests
            )
        if case in {"correction", "forget"}:
            outcomes["old_record_not_recalled"] = all(
                original_id not in (row[field] or [])
                for row in manifests
                for field in (
                    "recalled_memory",
                    "facts",
                    "memory",
                    "recent_session_artifacts",
                )
            )
    return outcomes, original_id


def _campaign_prompt(
    case: str, phase: str, tokens: dict[str, str], record_id: str | None
) -> str:
    if phase == "recall":
        return (
            "What is the exact continuity code? Reply only with the code, or UNKNOWN "
            "if it is not available. Do not guess or inspect other sessions."
        )
    if phase == "forget":
        return (
            f"Use memory.forget on record_id={record_id} with reason='requested removal'. "
            "Do not search for or repeat its contents. Reply only done."
        )
    if case == "automatic":
        return f"The continuity code is {tokens['old']}. Acknowledge briefly."
    token = tokens["new"] if phase == "correct" else tokens["old"]
    prompt = (
        "Use memory.write, omit scope, record_type=fact, key=fact:continuity_code, "
        f"title=Continuity code, content={token}. Reply only stored when successful."
    )
    if case == "relevance":
        prompt += (
            " Also store a separate unrelated fact with key=fact:weather, title=Weather, "
            f"content={tokens['distractor']}."
        )
    return prompt
