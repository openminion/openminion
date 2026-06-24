# Red-team probes

This tree holds adversarial fixtures and probe harnesses for OpenMinion security/integrity gates.

## Layout

- `fixtures/` — sealed, committed corpora (JSONL) and the deterministic generator scripts that produce them. Corpora are committed verbatim so probe runs are reproducible across machines and time.
- `probes/` — runner harnesses. Each probe loads a sealed corpus, replays it through the gate under test, and emits a `summary.json` artifact under `.openminion/runtime/<probe-id>-<YYYYMMDD>-feasibility/`.

## Per-lane mapping

| Lane | Spec | Tracker | Fixture | Probe runner |
| --- | --- | --- | --- | --- |
| Memory-poisoning-defense (MPD) | Memory-poisoning-defense spec | Memory-poisoning-defense tracker | `fixtures/memory_injection_corpus_v1.jsonl` | `probes/mpd_feasibility_probe.py`, `probes/mpd_closeout_smoke.py` |

## Discipline

1. Fixtures are **committed verbatim**. Never regenerate at run time. The generator script exists for reproducibility/audit, not for runtime use.
2. Probe harnesses start with corpus loading, iteration, and artifact
   emission only. Gate logic lands in the lane's implementation tasks.
3. Probe artifacts go under `.openminion/runtime/<probe-id>-<YYYYMMDD>-feasibility/summary.json` — same convention as prior CVSF/PAC artifacts.
4. Lane discipline: if a probe falls below its feasibility threshold, the owning lane chooses `retain_design_only` (PAC precedent). Do NOT lower thresholds post-hoc to make a probe pass.
