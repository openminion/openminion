# `modules/artifact/`

Owner: `openminion-artifact`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

Content-addressed artifact storage and integrity contracts. Owns the
canonical artifact metadata schema, reference-edge graph helpers, and
the SQLite-backed artifact store. Other modules consume `ArtifactCtl`
to write/read artifacts and to express artifact references in their
own records.

## Scope

- `ArtifactCtl` service + `ArtifactCtlInterface` Protocol
- Artifact metadata schema (`ArtifactMeta`, `ArtifactRef`, `ViewRecord`)
- Reference-edge graph utilities (`refs.py`)
- Persistent artifact store backends (`storage/`)
- Versioned compatibility check via `ensure_artifact_compatibility`

## Non-goals

- Generic blob storage independent of artifact identity (lives in
  `modules/storage/`)
- Artifact rendering / preview rendering for UI surfaces
- Cross-process artifact sync; this module is in-process only

## Public surface

Re-exported from `openminion.modules.artifact`:

- `ArtifactCtl`, `ArtifactCtlConfig`, `ArtifactCtlError`,
  `ArtifactCtlInterface`, `ARTIFACT_INTERFACE_VERSION`,
  `ensure_artifact_compatibility`
- Records: `ArtifactMeta`, `ArtifactRef`, `ViewRecord`
- `load_config`

## Dependencies

- `modules/storage/` — blob/record store primitives
- `base/` — config, paths, errors

## Canonical shape

Follows the canonical pattern with a precise control-surface owner:
`control.py` houses `ArtifactCtl` and the ingest/view/retention
helpers behind that public facade.
