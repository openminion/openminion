---
name: social_post
id: social_post
tools: [http_request]
tags: [social, twitter, linkedin, posting]
metadata:
  short-description: Post content to social media platforms via their APIs
---

# Summary
Compose and publish posts to Twitter/X or LinkedIn via their REST APIs using stored credentials.

# Procedure
- Read the target platform and credentials from config.toml (keys: platform, api_key, api_secret, access_token).
- Compose the post text from the provided topic or draft, keeping within platform character limits (280 for X, 3000 for LinkedIn).
- For X: POST to https://api.twitter.com/2/tweets with body {"text": "..."} and Authorization: Bearer header.
- For LinkedIn: POST to https://api.linkedin.com/v2/ugcPosts with the author URN and shareMediaCategory NONE.
- Check the response status code: 201 (X) or 201 (LinkedIn) indicates success.
- Extract and return the post ID from the response body.

# Verification
- Confirm the response contains a post ID (id field for X, id field for LinkedIn).
- If status is 401 confirm credentials are set. If 422 confirm text length is within limits.
