---
id: research-brief-brave
name: Research Brief (Brave)
version: 0.0.1
description: Produce a concise, cited brief using Brave Search.
tags: [research, citations]
tools: [web.search, file]
scopes_required: ["tool.execute", "tool.net.connect"]
risk: medium
when_to_use:
  - "User asks to look up latest info"
  - "Need citations or sources"
verification:
  - "At least 3 citations are present"
  - "Recency is explicitly described"
inputs:
  - name: topic
    type: string
outputs:
  - name: brief_md
    type: artifact_ref
---

# Skill Card
- **Goal:** Produce a concise brief with citations and recency notes.
- **Triggers:** Research tasks requiring sources.
- **Tools:** web.search, file
- **Hard rules:** Include at least 3 sources and publish dates when possible.

# Procedure
## Step 1 - Query set
Run up to three searches when needed: topic, topic latest, topic controversy.
For narrow latest-news or top-N requests, stop after one or two searches if result titles/snippets already provide enough evidence to answer.

## Step 2 - Source selection
Select up to five credible sources, preferring primary references.

## Step 3 - Brief output
Write Summary, Key points, What changed, and Links.

# Checks
- At least 3 citations included.
- Recency is explicitly addressed.

# Failure & Recovery
- If sources conflict, present both views and mark uncertainty.
