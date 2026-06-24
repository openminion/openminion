---
id: cli-chat-smoke-web-research
name: CLI Chat Smoke Test - Web Research
version: 0.0.1
description: Simple web research skill for CLI chat smoke testing with source summary.
tags: [research, web, search, smoke-test, cli]
tools: [web.search, web.fetch]
scopes_required: ["tool.execute"]
risk: low
when_to_use:
  - "User asks for research on a topic"
  - "User requests a summary from web sources"
  - "Smoke testing research skill domain"
inputs:
  - name: research_topic
    type: string
    description: The topic or question to research
outputs:
  - name: research_summary
    type: artifact_ref
    description: Reference to the research summary with sources
---

# Skill Card
- **Goal:** Conduct web research and produce source-backed summary for CLI chat smoke testing.
- **Triggers:** Research requests, information gathering, smoke test scenarios.
- **Tools:** web.search, web.fetch
- **Hard rules:** Use 2-3 sources; cite URLs; provide balanced summary; note confidence level.

# Procedure

## Step 1 - Clarify research scope
Restate the topic and confirm scope (definitions, comparisons, current status, etc.).

## Step 2 - Search for sources
Use web.search to find 2-3 authoritative sources on the topic.

## Step 3 - Fetch and extract key points
Use web.fetch to read sources and extract key findings.

## Step 4 - Synthesize summary
Combine findings into balanced summary with inline citations.

## Step 5 - Output with metadata
Produce Markdown with:
- Research question restated
- Summary with citations
- Source list with URLs
- Confidence level (high/medium/low)

# Checks
- Uses 2-3 distinct sources
- Cites URLs inline
- Provides balanced view (not one-sided)
- States confidence level

# Failure & Recovery
- If search returns no results, try alternative keywords.
- If sources conflict, note the disagreement and explain which is more authoritative.
