- Describe the problem and the resulting behavior.
- Link related reports with non-closing references where release verification remains open.

Validation

- List the exact commands and results, including any checks not run.

Contributor review

- [ ] I followed [Code Quality and Hygiene](https://github.com/openminion/openminion/blob/dev/CODE_QUALITY.md), reviewed the complete final diff and each new delta, and recorded the head and caller/failure evidence for new layers, defensive checks, fallbacks or retries.
- [ ] I removed unnecessary structure and redundant comments/docstrings in source, tests and scripts while preserving required validation, permissions, compatibility, persistence, replay, cleanup, public descriptions and notices/directives.
- [ ] I updated affected public documentation and kept unrelated cleanup out of this PR.
- [ ] For cleanup, I linked the current inventory and per-file dispositions, validation and remaining work; for merged-code findings, I checked surviving code and existing owners on current `dev`.
