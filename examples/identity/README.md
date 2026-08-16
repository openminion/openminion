# Identity Examples

This folder contains sample identity profiles for `openminion identity upsert`.

## Load a sample profile

From the package root, initialize a local configuration once and load the
checked-in profile:

```bash
openminion config init --provider echo
openminion identity upsert examples/identity/sample.yaml
```

## Verify profile and rendered snippet

```bash
openminion identity show sample
openminion identity render sample --purpose act --max-tokens 180
```

## Interactive CLI controls

Inside bare `openminion`:

```text
/identity list
/identity show
/identity render
/identity upsert path/to/profile.yaml
/identity delete <profile-id>
```
