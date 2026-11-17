"""Put the platform's shared packages on `sys.path` for the whole suite.

Every test module already inserts its own `src` path, because the README runs
them one at a time as plain scripts (`uv run python .../test_gateway.py`) where
no conftest is loaded. What a module cannot do for itself is locate
`platform/common`: its position relative to the test file changes whenever a
tool runs the suite from a copied tree -- mutation testing copies `src/` and
`tests/` into `mutants/`, which adds a directory level. Searching upwards for
the directory that actually holds the package is stable under both layouts.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ancestor_holding(marker: str) -> Path | None:
    """The nearest ancestor directory that contains `marker`."""
    for parent in Path(__file__).resolve().parents:
        if (parent / marker).exists():
            return parent
    return None


for _marker in ("common/http.py", "masking_engine/__init__.py"):
    _holder = _ancestor_holding(_marker)
    if _holder is not None and str(_holder) not in sys.path:
        sys.path.insert(0, str(_holder))
