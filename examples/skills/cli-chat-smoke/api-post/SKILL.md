---
id: cli-chat-smoke-api-post
name: CLI Chat Smoke Test - API Post Workflow
version: 0.0.1
description: Simple API posting skill for CLI chat smoke testing with request/response handling.
tags: [api, http, post, workflow, smoke-test, cli]
tools: [http_request]
scopes_required: ["tool.execute"]
risk: medium
when_to_use:
  - "User asks to post data to an API"
  - "User requests API workflow execution"
  - "Smoke testing API skill domain"
inputs:
  - name: api_endpoint
    type: string
    description: The API endpoint URL
  - name: payload
    type: object
    description: The data to post
outputs:
  - name: api_response
    type: artifact_ref
    description: Reference to the API response summary
---

# Skill Card
- **Goal:** Execute API POST workflow with proper error handling for CLI chat smoke testing.
- **Triggers:** API requests, data posting, smoke test scenarios.
- **Tools:** http_request
- **Hard rules:** Validate URL; check response status; handle errors gracefully; document request/response.

# Procedure

## Step 1 - Validate endpoint
Confirm the API URL is well-formed and reachable.

## Step 2 - Prepare payload
Structure the request body according to the API contract.

## Step 3 - Execute POST
Use http_request to send the POST with proper headers.

## Step 4 - Handle response
Check status code: 2xx = success, 4xx = client error, 5xx = server error.

## Step 5 - Document result
Produce Markdown with:
- Request summary (endpoint, method)
- Response status and key data
- Any errors or warnings

# Checks
- URL is validated before request
- Response status is checked and categorized
- Errors handled gracefully (no crash)
- Request and response documented

# Failure & Recovery
- If URL is invalid, ask for correct endpoint.
- If request fails (timeout, connection error), retry once or report clearly.
- If response is 4xx/5xx, explain the error and suggest fixes.
