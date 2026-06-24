---
id: api-account-publish-share
name: API Account Publish Share
version: 0.0.1
description: Create post and share using an existing account through a mock or sandbox API.
tags: [api, post, share, existing-account]
tools: [http_request, file]
scopes_required: ["tool.http_request", "tool.fs.write"]
risk: medium
when_to_use:
  - "Publish to existing account"
  - "Share post with existing account"
  - "Continue from account creation"
verification:
  - "Post created with valid ID returned"
  - "Share response includes share_url"
inputs:
  - name: account_id
    type: string
    description: Existing account ID to use
  - name: post_content
    type: string
    description: Content for the post to create
  - name: mock_api_url
    type: string
    default: "https://mock-api.example.test"
    description: Base URL for a mock or sandbox API service
outputs:
  - name: post_id
    type: string
  - name: share_url
    type: string
---

# Skill Card
- **Goal:** Create post and share using existing account (continuation flow)
- **Triggers:** "publish to existing account", "share post with existing account"
- **Tools:** http_request, file
- **Hard rules:**
  - Use a mock or sandbox API by default
  - Never call a production service unless the user explicitly requests it
  - Verify account exists before creating post
  - Redact all secrets in logs

# Procedure
## Step 1 - Validate environment
Check that the configured mock or sandbox API is available.
- Send GET to `{mock_api_url}/health`
- If fails, abort with clear error message

## Step 2 - Verify account exists
Send GET to `{mock_api_url}/api/v1/accounts/{account_id}`.
- If returns 404, abort with "Account not found"
- If returns valid account, proceed

## Step 3 - Create post
Send POST to `{mock_api_url}/api/v1/accounts/{account_id}/posts` with JSON body:
```json
{
  "content": "{post_content}",
  "title": "Auto-created post"
}
```
Store the returned `post_id`.

## Step 4 - Share post
Send POST to `{mock_api_url}/api/v1/posts/{post_id}/share` with empty body `{}`.
Store the returned `share_url`.

## Step 5 - Log results
Write summary to output file with:
- Account ID used
- Post ID
- Share URL
- Timestamp

# Checks
- Health check returns 200
- Account exists check returns valid account data
- Post creation returns valid `id` starting with `post_`
- Share returns valid `share_url` starting with `http`

# Failure & Recovery
- **Mock or sandbox API unavailable**: Abort with clear error
- **Account not found**: Abort with clear error; do not create new account
- **Post creation fails**: Log error, abort flow
- **Share fails**: Log error, still report post_id
- **Network timeout**: Retry once with exponential backoff (max 2 retries)
