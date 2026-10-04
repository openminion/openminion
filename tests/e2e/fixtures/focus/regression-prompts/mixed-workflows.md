# Mixed Workflow Scenarios

## `LH-MIX-001` Research To Working Code

- Version: 1
- Tags: research, planning, coding, tools, verification
- Prerequisites: network tools and an empty scratch directory with Python

Prompt:

> Research the minimum current requirements for reading RSS and Atom feeds safely with Python's standard library, choose a bounded design, and implement a small `feed_titles.py` CLI plus focused tests in the current directory; preserve authoritative source links, explain which research findings shaped the design, avoid third-party dependencies and network access in tests, run the tests and a fixture-backed CLI check, repair failures, and finish only when the implementation and evidence agree.

Pass evidence:

1. Research is bounded and directly informs implementation choices.
2. Tests use local fixtures and cover both supported feed shapes.
3. The CLI and tests are executed, not merely described.
4. The final explanation links decisions to evidence and actual validation.

Failure signals:

1. Research continues after the design question is resolved.
2. Tests depend on live network responses.
3. Source claims and implementation behavior contradict one another.

## `LH-MIX-002` Diagnose, Repair, And Verify An Incident

- Version: 1
- Tags: operations, logs, research, coding, recovery, verification
- Prerequisites: a disposable service fixture with logs, configuration, and a reproducible health check

Prompt:

> Diagnose the failing service in the current fixture by inspecting its logs, configuration, recent changes, and health check, research unfamiliar error facts only when local evidence is insufficient, form and test a root-cause hypothesis, apply the smallest reversible repair, restart or rerun the service as appropriate, verify the original health check and one negative path, and produce an incident summary that separates observed facts, inference, actions, validation, and remaining risk.

Pass evidence:

1. Diagnosis begins with local evidence and distinguishes fact from inference.
2. External research is used only to resolve a concrete uncertainty.
3. The repair is minimal, reversible, and tied to the root cause.
4. Post-action verification checks both recovery and a relevant failure path.

Failure signals:

1. Configuration is changed before evidence supports a hypothesis.
2. Restart success is treated as proof without the health check.
3. Logs or external documentation are quoted without interpreting relevance.
