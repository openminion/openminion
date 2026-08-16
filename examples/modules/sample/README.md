# Sample Module

Type: `example-package`
Runtime peer: none

This package owns the minimal sample module used as a framework example.
It is not part of the runtime `openminion.modules` package tree.
Primary contracts: `interfaces.py`, `service.py`, `provider.py`. This sample
package has no dedicated event family and exists to demonstrate module
framework shape, integration wiring, and CLI patterns.

From the package root:

```bash
PYTHONPATH=examples/modules python -m sample health
PYTHONPATH=examples/modules python -m sample list
PYTHONPATH=examples/modules python -m sample test --provider uppercase --input hello
```
