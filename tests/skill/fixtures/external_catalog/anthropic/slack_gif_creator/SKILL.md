---
name: slack_gif_creator
id: slack_gif_creator
tools: [http_request]
tags: [slack, media]
---

# Summary
Create and post animated GIF responses in Slack channels from agent workflows.

# Procedure
- Select or generate the GIF content matching the conversation context.
- Upload the GIF asset via the Slack files API endpoint.
- Post the message with the GIF attachment to the target channel.

# Verification
- Confirm the GIF appears in the channel and the file upload succeeded.
