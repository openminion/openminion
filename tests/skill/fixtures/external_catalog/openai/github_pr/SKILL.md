---
name: github_pr
id: github_pr
tools: [http_request, file]
tags: [github, pull-request, review, api]
metadata:
  short-description: Review a GitHub pull request via the GitHub REST API and post a summary comment
---

# Summary
Fetch a GitHub pull request's diff and review comments, summarize findings, and post a review comment via the API.

# Procedure
- Read repo owner, repo name, PR number, and GITHUB_TOKEN from config.toml.
- Fetch PR metadata: GET https://api.github.com/repos/{owner}/{repo}/pulls/{number} with Authorization: Bearer {token}.
- Fetch the PR diff: GET the same URL with Accept: application/vnd.github.v3.diff header.
- Fetch existing review comments: GET https://api.github.com/repos/{owner}/{repo}/pulls/{number}/comments.
- Summarize the changes: files changed, lines added/removed, open comment threads.
- Write the summary to pr-review-{number}.md.
- Post the summary as a PR comment: POST https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments with body {"body": "<summary>"}.

# Verification
- Confirm the PR metadata fetch returned 200 and the title field is populated.
- Confirm pr-review-{number}.md exists and contains a summary section.
- Confirm the comment POST returned 201 and the id field is set in the response.
