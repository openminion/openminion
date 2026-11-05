- Describe the problem and the resulting behavior.
- Link related reports with non-closing references where release verification remains open.

Validation

- List the exact commands and results, including any checks not run.

Contributor review

- [ ] I followed [Code Quality and Hygiene](https://github.com/openminion/openminion/blob/dev/CODE_QUALITY.md) and reviewed the final diff for existing-owner reuse, simple control flow, and unnecessary duplication. Each new layer, fallback or retry serves a concrete caller or failure case.
- [ ] I removed redundant comments/docstrings in source and tests while preserving necessary rationale, public contracts, and required notices/directives.
- [ ] I updated affected public documentation and kept unrelated cleanup out of this PR.
