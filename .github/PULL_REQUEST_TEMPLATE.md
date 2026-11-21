- Describe the supported problem, its impact and the resulting behavior.
- Link related reports with non-closing references where release verification remains open.

Self-review evidence

<!-- Complete CODE_QUALITY.md#required-self-review-before-submission before requesting review.
Keep this short and specific. For docs-only changes, name the source or command checked;
mark runtime regression evidence not applicable with a reason. -->
- Reviewed head and base:
- Changed files or groups, actual caller/owner, and contracts preserved:
- Simplicity findings resolved; rationale for any non-obvious retained layer or defense:
- Regression control and relevant unchanged neighbors; limitations or deferred work:

Validation

- List exact commands and results, checks not run, and the revision tested.
- Label local, hosted, installed-package and live-provider evidence separately where applicable.

Contributor review

- [ ] I completed the [required self-review](https://github.com/openminion/openminion/blob/dev/CODE_QUALITY.md#required-self-review-before-submission) on the full final diff and each new delta, including comments/docstrings in source, tests and scripts.
- [ ] I used the smallest clear change in the existing owner, justified new structure or defenses against actual callers/failures, and preserved affected behavior and boundary contracts.
- [ ] My controls exercise the affected path; I did not weaken assertions, add skips/xfails, retry failures or raise quality ceilings to obtain a green result.
- [ ] I updated affected public guidance and release notes, kept unrelated cleanup out, and labelled historical evidence accurately.
- [ ] For a broad cleanup claim, I linked the live inventory, per-file dispositions and remaining work; otherwise I explained why it does not apply.
