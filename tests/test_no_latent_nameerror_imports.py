"""Every module here must import in an environment WITHOUT dsa110_contimg.

Grows phase-by-phase during the contimg-import-retirement migration
(docs/rse/specs/plan-contimg-import-retirement.md). A failure means a
soft-imported legacy name is used unconditionally (the latent-NameError
bug class) or a retarget was missed.
"""
import importlib

import pytest

PHASE1_MODULES = [
    "dsa110_continuum.calibration.precompute.precompute",
    "dsa110_continuum.calibration.rfi_adaptive_enhanced",
    "dsa110_continuum.evaluation.database",
    "dsa110_continuum.evaluation.harness",
    "dsa110_continuum.evaluation.stages",
    "dsa110_continuum.pointing.monitor",
    "dsa110_continuum.qa.calibration_stability_tracker",
    "dsa110_continuum.qa.pipeline_hooks",
    "dsa110_continuum.imaging.fov",
]

PHASE3_MODULES = [
    "dsa110_continuum.calibration.rfi_adaptive_thresholds",
    "dsa110_continuum.catalog.flux_monitoring",
    "dsa110_continuum.catalog.spectral_index",
    "dsa110_continuum.catalog.variable_source_detection",
    "dsa110_continuum.unified_config",
]
PHASE1_MODULES += PHASE3_MODULES


@pytest.mark.parametrize("mod", PHASE1_MODULES)
def test_module_imports_without_legacy_package(mod):
    importlib.import_module(mod)
