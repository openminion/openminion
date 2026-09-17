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

A successful production PyPI release prepares a metadata-only PR for main.
Only protected merge makes its feed discoverable. Use a merge commit, not
squash/rebase, to preserve referenced record SHAs. Verify public readback,
then back-merge main into dev without regenerating metadata from dev HEAD.
Never overwrite retained records or publish prototype binary candidates.

See [the next-release checklist](../../../RELEASING.md#next-release-runtime-metadata-checklist).
