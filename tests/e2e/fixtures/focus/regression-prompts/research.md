# Research Scenarios

## `LH-RES-001` Evidence-Based Technical Recommendation

- Version: 1
- Tags: retrieval, planning, primary-sources, synthesis, citations
- Prerequisites: network search and fetch tools

Prompt:

> Determine whether this project should adopt reproducible Python lock files for development and release workflows, research the current authoritative packaging guidance and at least two viable approaches, identify meaningful tradeoffs and contradictory evidence, and recommend the smallest practical direction for this repository; keep a brief research plan, use primary sources where available, preserve citations, stop searching when the decision is supported, and state uncertainty or missing evidence instead of inventing certainty.

Pass evidence:

1. A bounded research plan and purposeful follow-up queries.
2. At least three relevant sources, with primary sources preferred.
3. Source-backed comparison, contradiction handling, and a concrete recommendation.
4. Citations that resolve to the claims they support.
5. Honest limits when evidence or network access is unavailable.

Failure signals:

1. Repeated searches without synthesis.
2. Unsupported claims, decorative citations, or stale evidence presented as current.
3. Premature completion before comparing alternatives.

## `LH-RES-002` Multi-Stage Risk Brief

- Version: 1
- Tags: research, source-combination, risk-analysis, context-continuity
- Prerequisites: network search and fetch tools

Prompt:

> Research the current operational risks of allowing autonomous coding agents to run unattended for eight hours in a software repository, organize the work into security, correctness, cost, state-continuity, and recovery questions, gather evidence from authoritative sources, revise the plan when findings change the risk model, and produce a prioritized mitigation brief that separates framework controls from provider or infrastructure limits and clearly identifies which risks remain unproven.

Pass evidence:

1. The original objective remains visible through multiple research stages.
2. Evidence is combined across the requested risk areas without losing source attribution.
3. The plan changes when evidence warrants it.
4. The final priorities distinguish product, provider, and environment ownership.

Failure signals:

1. A generic safety essay with no evidence trail.
2. Context drift into unrelated agent comparisons.
3. Provider limitations reported as framework defects or vice versa.
