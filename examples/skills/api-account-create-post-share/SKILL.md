---
id: api-account-create-post-share
name: API Account Create Post Share
version: 0.0.1
description: Full flow to create account, create post, and share post through a mock or sandbox API.
tags: [api, account, post, share]
tools: [http_request, file]
scopes_required: ["tool.http_request", "tool.fs.write"]
risk: medium
when_to_use:
  - "Create account and publish content"
  - "Full account lifecycle flow"
  - "Test API orchestration with mock service"
verification:
  - "Account created with valid ID returned"
  - "Post created with valid ID returned"
  - "Share response includes share_url"
inputs:
  - name: account_name
    type: string
    description: Name for the new account
  - name: post_content
    type: string
    description: Content for the post to create
  - name: mock_api_url
    type: string
    default: "https://mock-api.example.test"
    description: Base URL for a mock or sandbox API service
outputs:
  - name: account_id
    type: string
  - name: post_id
    type: string
  - name: share_url
    type: string
---

# Skill Card
- **Goal:** Execute complete account lifecycle: create account → create post → share post
- **Triggers:** "create account and publish", "create account post and share"
- **Tools:** http_request, file
- **Hard rules:**
  - Use a mock or sandbox API by default
  - Never call a production service unless the user explicitly requests it
  - Redact all secrets in logs
  - Verify each step before proceeding to next

# Procedure
## Step 1 - Validate environment
Check that the configured mock or sandbox API is available.
- Send GET to `{mock_api_url}/health`
- If fails, abort with clear error message

## Step 2 - Create account
Send POST to `{mock_api_url}/api/v1/accounts` with JSON body:
```json
{
  "name": "{account_name}",
  "email": "test_{timestamp}@example.com"
}
```
Store the returned `account_id` and `api_key`.

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
- Account ID (redacted api_key)
- Post ID
- Share URL
- Timestamps for each step

# Checks
- Health check returns 200
- Account creation returns valid `id` starting with `acc_`
- Post creation returns valid `id` starting with `post_`
- Share returns valid `share_url` starting with `http`

# Failure & Recovery
- **Mock or sandbox API unavailable**: Abort with clear error; do not fallback to a production API
- **Account creation fails**: Log error, abort flow (cannot proceed without account)
- **Post creation fails**: Log error with account_id, abort flow
- **Share fails**: Log error with account_id and post_id, still report post_id
- **Network timeout**: Retry once with exponential backoff (max 2 retries)

# Secret Handling
- API keys returned from mock API must be redacted in logs
- Use format: `sk_abc123***` (first 8 chars + ***)
- Never write raw api_key to output files
