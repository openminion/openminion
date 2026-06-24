---
name: web_signup
id: web_signup
tools: [browser, file]
tags: [browser, signup, forms, onboarding]
metadata:
  short-description: Complete web-based account signup or registration flows via browser automation
---

# Summary
Navigate to a registration page, fill in signup form fields, submit, and confirm account creation.

# Procedure
- Read the target URL, username, email, and password from config.toml.
- Open the signup URL in the browser.
- Locate the email input (common selectors: input[type=email], #email, [name=email]) and fill the value.
- Locate the password input (input[type=password]) and fill the value.
- If a username or display name field is present, fill it from config.
- Click the submit button (button[type=submit], input[type=submit], or button containing "Sign up" / "Register").
- Wait for redirect or confirmation message (success indicators: dashboard URL, "Welcome", "Account created", email verification prompt).
- Write the resulting session state or confirmation URL to signup-result.txt.

# Verification
- Confirm the browser URL changed from the signup page.
- Confirm no error messages are visible (red banners, "email already in use", "invalid password").
- Confirm signup-result.txt was written with the confirmation URL or message.
