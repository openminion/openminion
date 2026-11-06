# Instructions for coding agents

Read [CONTRIBUTING.md](CONTRIBUTING.md) and
[CODE_QUALITY.md](CODE_QUALITY.md) before changing this repository. Follow the
linked engineering and validation guidance for the affected owner.

The quality rules apply to every change, including agent and maintainer work.
Use the minimum usable code in the existing owner. Review the complete final
diff and each new delta for speculative abstractions, unnecessary defensive
branches, duplication and redundant comments or docstrings. Preserve real
trust-boundary, permission, compatibility, persistence, replay and cleanup
contracts.

Record the reviewed revision, evidence and outstanding findings. For merged
code and broad cleanup, follow the current-source and per-file ledger rules in
CODE_QUALITY.md; do not claim package-wide coverage from a sampled review.
Run the repository's required checks and do not bypass unresolved findings or
weaken validation to obtain a green result.
