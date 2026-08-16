# Module Examples

This folder holds example-only module packages that demonstrate framework
patterns without being imported as part of the runtime package tree.

Current examples:

1. `sample/` — reference implementation for the example module contracts.
   Run it by adding `openminion/examples/modules` to `PYTHONPATH` and
   invoking one of the direct commands:

   ```bash
   PYTHONPATH=examples/modules python -m sample health
   PYTHONPATH=examples/modules python -m sample list
   PYTHONPATH=examples/modules python -m sample test --provider uppercase --input hello
   ```
