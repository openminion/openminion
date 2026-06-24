# Identity Examples

This folder contains sample identity profiles for `openminion identity upsert`.

## Load a sample profile

From the package root:

```bash
PYTHONPATH=src .venv/bin/python3.11 -m openminion identity upsert examples/identity/<profile>.yaml
```

Replace `<profile>` with the checked-in sample profile or your own profile
file.

## Verify profile and rendered snippet

```bash
PYTHONPATH=src .venv/bin/python3.11 -m openminion identity show <profile-id>
PYTHONPATH=src .venv/bin/python3.11 -m openminion identity render <profile-id> --purpose act --max-tokens 180
```

## Chat CLI controls

Inside `openminion chat`:

```text
/identity list
/identity show
/identity render
/identity upsert path/to/profile.yaml
/identity delete <profile-id>
```
