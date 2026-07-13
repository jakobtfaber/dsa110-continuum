"""Package __init__ re-exports must resolve from the new namespace alone.

Phase 6 of the contimg-import-retirement migration
(docs/archive/contimg-retirement/plan-contimg-import-retirement.md): the legacy
``dsa110_contimg.core.*`` re-export layers are flipped to relative imports
of the sibling modules, so every ``__all__`` name resolves without the old
package installed.
"""
import importlib

import pytest

PACKAGES = [
    "dsa110_continuum.calibration",
    "dsa110_continuum.calibration.hardening",
    "dsa110_continuum.calibration.precompute",
    "dsa110_continuum.imaging",
    "dsa110_continuum.photometry",
    "dsa110_continuum.qa",
    "dsa110_continuum.simulation",
    "dsa110_continuum.validation",
    "dsa110_continuum.visualization",
]


@pytest.mark.parametrize("pkg", PACKAGES)
def test_package_exports_resolve_without_legacy(pkg):
    mod = importlib.import_module(pkg)
    missing = [n for n in getattr(mod, "__all__", []) if not hasattr(mod, n)]
    assert not missing, f"{pkg}: unresolved __all__ entries {missing}"
