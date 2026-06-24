# Red-team probe runners

This directory holds adversarial-replay harnesses for OpenMinion security gates.

## mpd_feasibility_probe.py

Feasibility-probe runner for the memory-poisoning-defense (MPD) lane.

- **Spec**: memory-poisoning-defense spec (section D5).
- **Tracker**: memory-poisoning-defense tracker (MPD-00a and MPD-CQ-01).
- **Fixture**: `tests/redteam/fixtures/memory_injection_corpus_v1.jsonl`.

### Status

Harness-only. The runner ships corpus loading, iteration, artifact aggregation,
and JSON output shape. The trust-gate decision logic (`decide` callable) lands
separately in MPD-00a step 3.

### Scaffolding self-test

The module's `main()` plugs in `decide_using_expected_fields()`, a self-oracle
stub that reads the expected outcome from each corpus row. It produces a
100%-matches-expected artifact that proves the plumbing works end-to-end
without measuring any real defense.

```bash
cd openminion
.venv/bin/python3.11 tests/redteam/probes/mpd_feasibility_probe.py
```

Expected output: an artifact at `.openminion/runtime/mpd-<YYYYMMDD>-feasibility/summary.json` with `block_rate = 0.60` (matching the expected composition from the corpus) and `decision = "unblock_full_lane"` (because 0.60 meets the 0.60 probe threshold).

### Real MPD-00a run

The real run is a separate execution that:

1. Implements `decide_real_minimum_viable_gate(candidate, state) -> tuple[str, str]` — provenance-score lookup + rate-limit pressure check + claim-key presence check. NO corroboration, NO contradiction, NO LLM-judge.
2. Constructs initial `state` with the pre-seeded source-window per the reference probe config (50 prior `llm_extracted` candidates already in the rolling 1-hour window starting at the corpus's `_SATURATED_WINDOW_START`).
3. Calls `run_probe(corpus, decide=decide_real_minimum_viable_gate, config=ProbeConfig(), artifact_root=...)`.
4. Writes the artifact to `.openminion/runtime/mpd-<YYYYMMDD>-feasibility/summary.json`.
5. Compares `block_rate` against `threshold_probe` (0.6). If below, the lane closes at `retain_design_only` per the spec D5 protocol.

That implementation work is out of scope for this tranche and lands with
MPD-00a.

## mpd_closeout_smoke.py

Closeout smoke runner for the same MPD lane.

- **Spec**: memory-poisoning-defense spec (section D6).
- **Tracker**: memory-poisoning-defense tracker (MPD-CQ-01).
- **Fixture**: `tests/redteam/fixtures/memory_injection_corpus_v1.jsonl`.

This runner uses the real `_promote_mature_candidates(...)` flow with the
composite scorer, shared rate limiter, and audit sink enabled. It writes
`.openminion/runtime/mpd-<YYYYMMDD>-closeout/summary.json` and is the
non-negotiable closeout gate for `wip -> qa`.

### Artifact shape

See section D5 of the memory-poisoning-defense spec plus the `ProbeArtifact` dataclass in `mpd_feasibility_probe.py`. The artifact mirrors the prior CVSF probe convention at `.openminion/runtime/cvsf-20260505-feasibility/summary.json` (top-level `artifact_root`, `attempts`, `decision`, `ok`, `results`) and extends it with MPD-specific fields (`blocked`, `block_rate`, `threshold`, `by_category`, `config`).

### Discipline

1. Never plug `decide_using_expected_fields()` into a real run. It is a
   self-oracle, so the resulting artifact says nothing about the defense.
2. Never modify the corpus to make a probe pass. If the probe falls below threshold, the lane closes at `retain_design_only` (PAC precedent).
3. Never lower the threshold post-hoc.
