SKILL_PACKAGE_NAME = "skill"
SKILL_URL_MAX_CONTENT_BYTES = 512 * 1024
SKILL_URL_MAX_CONTENT_CHARS = 50_000
SKILL_URL_FETCH_USER_AGENT = "openminion-skill-fetch/1.0"
# cap on HTTP redirect chain depth. Every redirect target is
# re-validated against the host blocklist before the next fetch attempt.
SKILL_URL_MAX_REDIRECTS = 3

__all__ = [
    "SKILL_PACKAGE_NAME",
    "SKILL_URL_FETCH_USER_AGENT",
    "SKILL_URL_MAX_CONTENT_BYTES",
    "SKILL_URL_MAX_CONTENT_CHARS",
    "SKILL_URL_MAX_REDIRECTS",
]
