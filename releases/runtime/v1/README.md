# OpenMinion runtime release metadata

These files describe published runtimes, not the current source checkout.
They live alongside code on `main`; normal `main` → `dev` back-merges retain
the same published metadata on the development branch.

```text
pypi/channels/stable.json
pypi/releases/<version>/<release-id>.json
binary/channels/stable.json
binary/releases/<version>/<release-id>.json
```

Desktop reads public `main` feeds over HTTPS. Each feed pins immutable records
by full commit URL and SHA-256. Empty feeds advertise no release. PyPI and
binary availability advance independently; null Desktop compatibility is not
an accepted Desktop update.

A successful production PyPI release prepares a source metadata-only PR for
main. Binary metadata is separate: a maintainer manually dispatches `Runtime
manifests` with the immutable `openminion/runtime` release tag and a unique
release ID. The verifier re-downloads every paired CLI/daemon asset, checks
GitHub's recorded digests and sizes, requires final native verification IDs and
non-null Desktop compatibility bounds, then prepares the same kind of protected
metadata PR.

Only protected merge makes either feed discoverable. Use a merge commit, not
squash/rebase, to preserve referenced record SHAs. Verify public readback, then
back-merge main into dev without regenerating metadata from dev HEAD. Never
overwrite retained records or publish private candidate records.

See [the next-release checklist](../../../RELEASING.md#next-release-runtime-metadata-checklist).
