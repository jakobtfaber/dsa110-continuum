"""Validation utilities for dsa110_contimg package."""

try:
    from dsa110_continuum.validation.package_health import run_diagnostics
except ImportError:
    pass  # optional deps of the target module absent (cloud/test env)

__all__ = ["run_diagnostics"]
