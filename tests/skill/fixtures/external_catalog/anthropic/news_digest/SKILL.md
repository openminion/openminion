---
name: news_digest
id: news_digest
tools: [web.search, http_request, file]
tags: [news, digest, research, posting, slack]
metadata:
  short-description: Search for news on a topic, summarize findings, and post a digest to Slack
---

# Summary
Search the web for recent news on a given topic, compile a digest summary, save it to a file,
and optionally post it to a Slack channel via webhook.

# Procedure
- Accept the topic and optional Slack webhook URL from the user or config.toml.
- Use web.search to find 5–10 recent articles on the topic (query: "{topic} news {current_year}").
- For each of the top 3 results, fetch the page content to extract key points.
- Compose a digest: title line, 3–5 bullet points summarizing key developments, source URLs.
- Write the digest to news-digest-{topic-slug}.md.
- If a Slack webhook URL is provided, POST the digest as a Slack message:
  POST {webhook_url} with body {"text": "<digest>"}.

# Verification
- Confirm web.search returned at least 3 results.
- Confirm news-digest-{topic-slug}.md exists with a non-empty bullet list.
- If Slack webhook was provided, confirm the POST returned 200 with body "ok".
